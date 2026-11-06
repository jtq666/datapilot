from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


class ConversationStore:
    """Local durable conversation store; production database adapter is pending."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS conversations (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    source_name TEXT NOT NULL,
                    knowledge_base_id TEXT,
                    title TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS conversations_owner_idx
                    ON conversations(workspace_id, owner_id, updated_at DESC);
                CREATE TABLE IF NOT EXISTS turns (
                    id TEXT PRIMARY KEY,
                    conversation_id TEXT NOT NULL REFERENCES conversations(id) ON DELETE CASCADE,
                    question TEXT NOT NULL,
                    answer TEXT,
                    run_id TEXT UNIQUE,
                    status TEXT NOT NULL CHECK(status IN ('running', 'succeeded', 'failed')),
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS turns_conversation_idx
                    ON turns(conversation_id, created_at);
                CREATE TABLE IF NOT EXISTS run_access (
                    run_id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _conversation(row: tuple) -> dict:
        return dict(zip(("id", "workspace_id", "owner_id", "source_name", "knowledge_base_id",
                         "title", "created_at", "updated_at"), row))

    def create(self, workspace_id: str, owner_id: str, source_name: str,
               knowledge_base_id: str | None, question: str) -> dict:
        conversation_id = str(uuid4())
        timestamp = now()
        title = question.strip()[:80]
        with self._connect() as db:
            db.execute("INSERT INTO conversations VALUES (?, ?, ?, ?, ?, ?, ?, ?)",
                       (conversation_id, workspace_id, owner_id, source_name,
                        knowledge_base_id, title, timestamp, timestamp))
        return self.get(workspace_id, owner_id, conversation_id)

    def get(self, workspace_id: str, owner_id: str, conversation_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT * FROM conversations WHERE id=? AND workspace_id=? AND owner_id=?",
                             (conversation_id, workspace_id, owner_id)).fetchone()
        return self._conversation(row) if row else None

    def list(self, workspace_id: str, owner_id: str, limit: int = 50) -> list[dict]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        with self._connect() as db:
            rows = db.execute("SELECT * FROM conversations WHERE workspace_id=? AND owner_id=? "
                              "ORDER BY updated_at DESC, id DESC LIMIT ?",
                              (workspace_id, owner_id, limit)).fetchall()
        return [self._conversation(row) for row in rows]

    def turns(self, workspace_id: str, owner_id: str, conversation_id: str) -> list[dict]:
        if self.get(workspace_id, owner_id, conversation_id) is None:
            raise LookupError("conversation not found")
        with self._connect() as db:
            rows = db.execute("SELECT id, question, answer, run_id, status, created_at FROM turns "
                              "WHERE conversation_id=? ORDER BY created_at, rowid",
                              (conversation_id,)).fetchall()
        return [dict(zip(("id", "question", "answer", "run_id", "status", "created_at"), row))
                for row in rows]

    def history(self, workspace_id: str, owner_id: str, conversation_id: str,
                max_turns: int = 10, max_chars: int = 24000) -> list[dict[str, str]]:
        completed = [turn for turn in self.turns(workspace_id, owner_id, conversation_id)
                     if turn["status"] == "succeeded" and turn["answer"]]
        selected = []
        used = 0
        for turn in reversed(completed[-max_turns:]):
            cost = len(turn["question"]) + len(turn["answer"])
            if selected and used + cost > max_chars:
                break
            selected.append(turn)
            used += cost
        messages = []
        for turn in reversed(selected):
            messages.extend(({"role": "user", "content": turn["question"]},
                             {"role": "assistant", "content": turn["answer"]}))
        return messages

    def begin_turn(self, workspace_id: str, owner_id: str, conversation_id: str,
                   question: str, run_id: str) -> str:
        turn_id = str(uuid4())
        timestamp = now()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            owner = db.execute("SELECT 1 FROM conversations WHERE id=? AND workspace_id=? AND owner_id=?",
                               (conversation_id, workspace_id, owner_id)).fetchone()
            if owner is None:
                raise LookupError("conversation not found")
            pending = db.execute("SELECT 1 FROM turns WHERE conversation_id=? AND status='running'",
                                 (conversation_id,)).fetchone()
            if pending:
                raise ValueError("conversation already has a running turn")
            db.execute("INSERT INTO turns VALUES (?, ?, ?, NULL, ?, 'running', ?)",
                       (turn_id, conversation_id, question, run_id, timestamp))
            db.execute("INSERT INTO run_access VALUES (?, ?, ?)", (run_id, workspace_id, owner_id))
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (timestamp, conversation_id))
        return turn_id

    def finish_turn(self, workspace_id: str, owner_id: str, conversation_id: str,
                    turn_id: str, answer: str | None, success: bool) -> None:
        if self.get(workspace_id, owner_id, conversation_id) is None:
            raise LookupError("conversation not found")
        with self._connect() as db:
            result = db.execute("UPDATE turns SET answer=?, status=? WHERE id=? AND conversation_id=? "
                                "AND status='running'", (answer, "succeeded" if success else "failed",
                                                        turn_id, conversation_id))
            if result.rowcount != 1:
                raise ValueError("turn is not running")
            db.execute("UPDATE conversations SET updated_at=? WHERE id=?", (now(), conversation_id))

    def delete(self, workspace_id: str, owner_id: str, conversation_id: str) -> bool:
        with self._connect() as db:
            result = db.execute("DELETE FROM conversations WHERE id=? AND workspace_id=? AND owner_id=?",
                                (conversation_id, workspace_id, owner_id))
        return result.rowcount == 1

    def run_owner(self, workspace_id: str, run_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT owner_id FROM run_access WHERE workspace_id=? AND run_id=?",
                             (workspace_id, run_id)).fetchone()
        return row[0] if row else None

    def run_owners(self, workspace_id: str, run_ids: list[str]) -> dict[str, str]:
        if not run_ids:
            return {}
        placeholders = ",".join("?" for _ in run_ids)
        with self._connect() as db:
            rows = db.execute(f"SELECT run_id, owner_id FROM run_access WHERE workspace_id=? "
                              f"AND run_id IN ({placeholders})", (workspace_id, *run_ids)).fetchall()
        return dict(rows)

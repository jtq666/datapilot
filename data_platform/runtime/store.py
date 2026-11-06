from __future__ import annotations

import json
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any
from uuid import uuid4

from .models import Run, RunEvent, RunStatus, utcnow


class RunStore:
    """Development implementation of the shared durable run contract.

    SQLite is used only for local tests. The production PostgreSQL adapter is pending.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        if self.path == ":memory:":
            raise ValueError("Use a file-backed database so runs survive reconnects")
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS runs (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    status TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 0
                );
                CREATE TABLE IF NOT EXISTS events (
                    id TEXT PRIMARY KEY,
                    run_id TEXT NOT NULL REFERENCES runs(id),
                    step_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    created_at TEXT NOT NULL,
                    payload TEXT NOT NULL,
                    sequence INTEGER NOT NULL,
                    UNIQUE(run_id, sequence)
                );
                CREATE TABLE IF NOT EXISTS checkpoints (
                    run_id TEXT PRIMARY KEY REFERENCES runs(id),
                    version INTEGER NOT NULL,
                    state TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def create_run(self, run_id: str, workspace_id: str, kind: str) -> Run:
        if not run_id or not workspace_id or not kind:
            raise ValueError("run_id, workspace_id and kind are required")
        now = utcnow()
        with self._connect() as db:
            db.execute(
                "INSERT INTO runs VALUES (?, ?, ?, ?, ?, ?, 0)",
                (run_id, workspace_id, kind, RunStatus.RUNNING, now.isoformat(), now.isoformat()),
            )
        return Run(run_id, workspace_id, kind, RunStatus.RUNNING, now, now)

    def get_run(self, run_id: str, workspace_id: str) -> Run | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id, workspace_id, kind, status, created_at, updated_at, version "
                "FROM runs WHERE id=? AND workspace_id=?",
                (run_id, workspace_id),
            ).fetchone()
        if row is None:
            return None
        return Run(
            row[0], row[1], row[2], RunStatus(row[3]),
            datetime.fromisoformat(row[4]), datetime.fromisoformat(row[5]), row[6],
        )

    def list_runs(self, workspace_id: str, limit: int = 50, offset: int = 0) -> list[Run]:
        if not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        if offset < 0:
            raise ValueError("offset must not be negative")
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, workspace_id, kind, status, created_at, updated_at, version "
                "FROM runs WHERE workspace_id=? ORDER BY created_at DESC, id DESC LIMIT ? OFFSET ?",
                (workspace_id, limit, offset),
            ).fetchall()
        return [Run(row[0], row[1], row[2], RunStatus(row[3]),
                    datetime.fromisoformat(row[4]), datetime.fromisoformat(row[5]), row[6]) for row in rows]

    def append_event(self, event: RunEvent, workspace_id: str) -> RunEvent:
        with self._connect() as db:
            owner = db.execute(
                "SELECT status FROM runs WHERE id=? AND workspace_id=?", (event.run_id, workspace_id)
            ).fetchone()
            if owner is None:
                raise LookupError("run not found in workspace")
            if RunStatus(owner[0]) in (RunStatus.SUCCEEDED, RunStatus.FAILED):
                raise ValueError("cannot append to a finished run")
            row = db.execute("SELECT sequence FROM events WHERE id=?", (event.id,)).fetchone()
            if row is not None:
                raise ValueError("duplicate event id")
            seq = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE run_id=?", (event.run_id,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (event.id, event.run_id, event.step_id, event.kind, event.created_at.isoformat(), json.dumps(event.payload), seq),
            )
        return RunEvent(event.id, event.run_id, event.step_id, event.kind, event.created_at, event.payload, seq)

    def events(self, run_id: str, workspace_id: str) -> list[RunEvent]:
        if self.get_run(run_id, workspace_id) is None:
            raise LookupError("run not found in workspace")
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, step_id, kind, created_at, payload, sequence "
                "FROM events WHERE run_id=? ORDER BY sequence", (run_id,)
            ).fetchall()
        return [
            RunEvent(row[0], run_id, row[1], row[2], datetime.fromisoformat(row[3]), json.loads(row[4]), row[5])
            for row in rows
        ]

    def save_checkpoint(self, run_id: str, workspace_id: str, state: dict[str, Any], expected_version: int) -> int:
        with self._connect() as db:
            now = utcnow().isoformat()
            changed = db.execute(
                "UPDATE runs SET version=version+1, updated_at=? "
                "WHERE id=? AND workspace_id=? AND version=? AND status IN (?, ?)",
                (now, run_id, workspace_id, expected_version, RunStatus.RUNNING, RunStatus.WAITING),
            ).rowcount
            if changed != 1:
                raise ValueError("run missing or checkpoint version conflict")
            version = expected_version + 1
            db.execute(
                "INSERT INTO checkpoints(run_id, version, state) VALUES (?, ?, ?) "
                "ON CONFLICT(run_id) DO UPDATE SET version=excluded.version, state=excluded.state",
                (run_id, version, json.dumps(state)),
            )
        return version

    def transition(
        self,
        run_id: str,
        workspace_id: str,
        expected_version: int,
        target: RunStatus,
        *,
        reason: str = "",
    ) -> Run:
        """Atomically change status and append its audit event.

        A completed run cannot be resumed; a failed run may be resumed only
        when a checkpoint exists. The caller must supply the observed version.
        """
        allowed = {
            RunStatus.RUNNING: {RunStatus.WAITING, RunStatus.SUCCEEDED, RunStatus.FAILED},
            RunStatus.WAITING: {RunStatus.RUNNING, RunStatus.FAILED},
            RunStatus.FAILED: {RunStatus.RUNNING},
            RunStatus.SUCCEEDED: set(),
        }
        target = RunStatus(target)
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            row = db.execute(
                "SELECT kind, status, created_at, version FROM runs WHERE id=? AND workspace_id=?",
                (run_id, workspace_id),
            ).fetchone()
            if row is None:
                raise LookupError("run not found in workspace")
            source = RunStatus(row[1])
            if expected_version != row[3]:
                raise ValueError("run version conflict")
            if target not in allowed[source]:
                raise ValueError(f"invalid run transition: {source} -> {target}")
            if source == RunStatus.FAILED and target == RunStatus.RUNNING:
                if db.execute("SELECT 1 FROM checkpoints WHERE run_id=?", (run_id,)).fetchone() is None:
                    raise ValueError("cannot resume a failed run without a checkpoint")
            now = utcnow()
            version = expected_version + 1
            db.execute(
                "UPDATE runs SET status=?, updated_at=?, version=? WHERE id=? AND workspace_id=?",
                (target, now.isoformat(), version, run_id, workspace_id),
            )
            sequence = db.execute(
                "SELECT COALESCE(MAX(sequence), 0) + 1 FROM events WHERE run_id=?", (run_id,)
            ).fetchone()[0]
            db.execute(
                "INSERT INTO events VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    str(uuid4()), run_id, "run", "status-changed", now.isoformat(),
                    json.dumps({"from": source, "to": target, "reason": reason}), sequence,
                ),
            )
        return Run(run_id, workspace_id, row[0], target, datetime.fromisoformat(row[2]), now, version)

    def load_checkpoint(self, run_id: str, workspace_id: str) -> tuple[int, dict[str, Any]] | None:
        if self.get_run(run_id, workspace_id) is None:
            raise LookupError("run not found in workspace")
        with self._connect() as db:
            row = db.execute("SELECT version, state FROM checkpoints WHERE run_id=?", (run_id,)).fetchone()
        return None if row is None else (row[0], json.loads(row[1]))


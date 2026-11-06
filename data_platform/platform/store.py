from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from uuid import uuid4


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


class FeatureStore:
    """Small durable catalog for platform resources.

    The catalog deliberately keeps resource payloads as JSON so the API can
    evolve without adding a new migration for every configuration screen.
    Every row is scoped by workspace; the caller still has to authorize the
    workspace before invoking a store method.
    """

    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as db:
            db.executescript(
                """
                CREATE TABLE IF NOT EXISTS resources (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    kind TEXT NOT NULL,
                    name TEXT NOT NULL,
                    version INTEGER NOT NULL DEFAULT 1,
                    payload TEXT NOT NULL,
                    enabled INTEGER NOT NULL DEFAULT 1,
                    created_at TEXT NOT NULL,
                    updated_at TEXT NOT NULL,
                    UNIQUE(workspace_id, kind, name)
                );
                CREATE INDEX IF NOT EXISTS resources_scope_idx
                    ON resources(workspace_id, kind, updated_at DESC);
                CREATE TABLE IF NOT EXISTS shares (
                    token TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    resource_type TEXT NOT NULL,
                    resource_id TEXT NOT NULL,
                    expires_at INTEGER NOT NULL,
                    revoked INTEGER NOT NULL DEFAULT 0,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS shares_scope_idx
                    ON shares(workspace_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS feedback (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    run_id TEXT NOT NULL,
                    user_id TEXT NOT NULL,
                    rating INTEGER NOT NULL CHECK(rating BETWEEN 1 AND 5),
                    comment TEXT NOT NULL DEFAULT '',
                    created_at TEXT NOT NULL,
                    UNIQUE(workspace_id, run_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS files (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    owner_id TEXT NOT NULL,
                    filename TEXT NOT NULL,
                    relative_path TEXT NOT NULL,
                    content_type TEXT NOT NULL,
                    size INTEGER NOT NULL,
                    sha256 TEXT NOT NULL,
                    created_at TEXT NOT NULL
                );
                CREATE INDEX IF NOT EXISTS files_scope_idx
                    ON files(workspace_id, created_at DESC);
                CREATE TABLE IF NOT EXISTS graph_entities (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    knowledge_base_id TEXT NOT NULL,
                    name TEXT NOT NULL,
                    entity_type TEXT NOT NULL,
                    properties TEXT NOT NULL,
                    UNIQUE(workspace_id, knowledge_base_id, name)
                );
                CREATE TABLE IF NOT EXISTS graph_relations (
                    id TEXT PRIMARY KEY,
                    workspace_id TEXT NOT NULL,
                    knowledge_base_id TEXT NOT NULL,
                    source_id TEXT NOT NULL,
                    target_id TEXT NOT NULL,
                    relation TEXT NOT NULL,
                    properties TEXT NOT NULL
                );
                """
            )

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    @staticmethod
    def _resource(row: tuple) -> dict:
        return {
            "id": row[0], "workspace_id": row[1], "kind": row[2], "name": row[3],
            "version": row[4], "payload": json.loads(row[5]), "enabled": bool(row[6]),
            "created_at": row[7], "updated_at": row[8],
        }

    def upsert(self, workspace_id: str, kind: str, name: str, payload: dict,
               *, enabled: bool = True) -> dict:
        if not workspace_id or not kind or not name.strip():
            raise ValueError("workspace, kind and name are required")
        now = _now()
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        with self._connect() as db:
            row = db.execute(
                "SELECT id, version, created_at FROM resources "
                "WHERE workspace_id=? AND kind=? AND name=?",
                (workspace_id, kind, name.strip()),
            ).fetchone()
            if row:
                db.execute(
                    "UPDATE resources SET version=?, payload=?, enabled=?, updated_at=? WHERE id=?",
                    (row[1] + 1, encoded, int(enabled), now, row[0]),
                )
                return self.get(workspace_id, kind, row[0])
            resource_id = str(uuid4())
            db.execute(
                "INSERT INTO resources VALUES (?, ?, ?, ?, 1, ?, ?, ?, ?)",
                (resource_id, workspace_id, kind, name.strip(), encoded, int(enabled), now, now),
            )
        return self.get(workspace_id, kind, resource_id)

    def list(self, workspace_id: str, kind: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, workspace_id, kind, name, version, payload, enabled, created_at, updated_at "
                "FROM resources WHERE workspace_id=? AND kind=? ORDER BY updated_at DESC, name",
                (workspace_id, kind),
            ).fetchall()
        return [self._resource(row) for row in rows]

    def get(self, workspace_id: str, kind: str, resource_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT id, workspace_id, kind, name, version, payload, enabled, created_at, updated_at "
                "FROM resources WHERE workspace_id=? AND kind=? AND id=?",
                (workspace_id, kind, resource_id),
            ).fetchone()
        return self._resource(row) if row else None

    def update(self, workspace_id: str, kind: str, resource_id: str,
               payload: dict, *, expected_version: int,
               name: str | None = None, enabled: bool | None = None) -> dict:
        """Update one catalog resource with optimistic concurrency control."""
        if expected_version < 1:
            raise ValueError("expected_version must be positive")
        encoded = json.dumps(payload, ensure_ascii=False, separators=(",", ":"))
        now = _now()
        with self._connect() as db:
            row = db.execute(
                "SELECT name, version, enabled FROM resources "
                "WHERE workspace_id=? AND kind=? AND id=?",
                (workspace_id, kind, resource_id),
            ).fetchone()
            if row is None:
                raise LookupError("resource not found")
            next_name = (name or row[0]).strip()
            next_enabled = int(row[2] if enabled is None else enabled)
            changed = db.execute(
                "UPDATE resources SET name=?, version=?, payload=?, enabled=?, updated_at=? "
                "WHERE workspace_id=? AND kind=? AND id=? AND version=?",
                (next_name, expected_version + 1, encoded, next_enabled, now,
                 workspace_id, kind, resource_id, expected_version),
            ).rowcount
            if changed != 1:
                raise ValueError("resource version conflict")
        return self.get(workspace_id, kind, resource_id) or {}

    def delete(self, workspace_id: str, kind: str, resource_id: str) -> bool:
        with self._connect() as db:
            result = db.execute(
                "DELETE FROM resources WHERE workspace_id=? AND kind=? AND id=?",
                (workspace_id, kind, resource_id),
            )
        return result.rowcount == 1

    def create_share(self, workspace_id: str, owner_id: str, resource_type: str,
                     resource_id: str, expires_at: int) -> dict:
        token = uuid4().hex + uuid4().hex
        with self._connect() as db:
            db.execute(
                "INSERT INTO shares VALUES (?, ?, ?, ?, ?, ?, 0, ?)",
                (token, workspace_id, owner_id, resource_type, resource_id, expires_at, _now()),
            )
        return self.share(token)

    def share(self, token: str) -> dict | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT token, workspace_id, owner_id, resource_type, resource_id, expires_at, revoked, created_at "
                "FROM shares WHERE token=?", (token,)
            ).fetchone()
        if not row:
            return None
        return dict(zip(("token", "workspace_id", "owner_id", "resource_type", "resource_id",
                         "expires_at", "revoked", "created_at"), row))

    def list_shares(self, workspace_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT token, workspace_id, owner_id, resource_type, resource_id, expires_at, revoked, created_at "
                "FROM shares WHERE workspace_id=? ORDER BY created_at DESC", (workspace_id,)
            ).fetchall()
        return [dict(zip(("token", "workspace_id", "owner_id", "resource_type", "resource_id",
                          "expires_at", "revoked", "created_at"), row)) for row in rows]

    def revoke_share(self, workspace_id: str, token: str) -> bool:
        with self._connect() as db:
            result = db.execute("UPDATE shares SET revoked=1 WHERE workspace_id=? AND token=?",
                                (workspace_id, token))
        return result.rowcount == 1

    def add_feedback(self, workspace_id: str, run_id: str, user_id: str,
                     rating: int, comment: str) -> dict:
        feedback_id = str(uuid4())
        with self._connect() as db:
            db.execute(
                "INSERT INTO feedback VALUES (?, ?, ?, ?, ?, ?, ?)",
                (feedback_id, workspace_id, run_id, user_id, rating, comment.strip(), _now()),
            )
        return {"id": feedback_id, "run_id": run_id, "rating": rating,
                "comment": comment.strip()}

    def list_feedback(self, workspace_id: str, run_id: str | None = None) -> list[dict]:
        query = "SELECT id, run_id, user_id, rating, comment, created_at FROM feedback WHERE workspace_id=?"
        args: list[str] = [workspace_id]
        if run_id:
            query += " AND run_id=?"
            args.append(run_id)
        query += " ORDER BY created_at DESC"
        with self._connect() as db:
            rows = db.execute(query, args).fetchall()
        return [dict(zip(("id", "run_id", "user_id", "rating", "comment", "created_at"), row))
                for row in rows]

    def add_file(self, workspace_id: str, owner_id: str, filename: str,
                 relative_path: str, content_type: str, size: int, sha256: str) -> dict:
        file_id = str(uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO files VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                       (file_id, workspace_id, owner_id, filename, relative_path,
                        content_type, size, sha256, _now()))
        return self.file(workspace_id, file_id) or {}

    def file(self, workspace_id: str, file_id: str) -> dict | None:
        with self._connect() as db:
            row = db.execute("SELECT id, workspace_id, owner_id, filename, relative_path, content_type, size, sha256, created_at "
                             "FROM files WHERE workspace_id=? AND id=?", (workspace_id, file_id)).fetchone()
        return dict(zip(("id", "workspace_id", "owner_id", "filename", "relative_path",
                         "content_type", "size", "sha256", "created_at"), row)) if row else None

    def list_files(self, workspace_id: str) -> list[dict]:
        with self._connect() as db:
            rows = db.execute("SELECT id, workspace_id, owner_id, filename, relative_path, content_type, size, sha256, created_at "
                             "FROM files WHERE workspace_id=? ORDER BY created_at DESC", (workspace_id,)).fetchall()
        return [dict(zip(("id", "workspace_id", "owner_id", "filename", "relative_path",
                          "content_type", "size", "sha256", "created_at"), row)) for row in rows]

    def delete_file(self, workspace_id: str, file_id: str) -> dict | None:
        item = self.file(workspace_id, file_id)
        if item:
            with self._connect() as db:
                db.execute("DELETE FROM files WHERE workspace_id=? AND id=?", (workspace_id, file_id))
        return item

    def add_entity(self, workspace_id: str, kb_id: str, name: str, entity_type: str,
                   properties: dict) -> dict:
        entity_id = str(uuid4())
        with self._connect() as db:
            row = db.execute("SELECT id FROM graph_entities WHERE workspace_id=? AND knowledge_base_id=? AND name=?",
                             (workspace_id, kb_id, name)).fetchone()
            if row:
                db.execute("UPDATE graph_entities SET entity_type=?, properties=? WHERE id=?",
                           (entity_type, json.dumps(properties, ensure_ascii=False), row[0]))
                entity_id = row[0]
            else:
                db.execute("INSERT INTO graph_entities VALUES (?, ?, ?, ?, ?, ?)",
                           (entity_id, workspace_id, kb_id, name, entity_type,
                            json.dumps(properties, ensure_ascii=False)))
        return {"id": entity_id, "name": name, "entity_type": entity_type, "properties": properties}

    def list_graph(self, workspace_id: str, kb_id: str) -> dict:
        with self._connect() as db:
            entities = db.execute("SELECT id, name, entity_type, properties FROM graph_entities WHERE workspace_id=? AND knowledge_base_id=? ORDER BY name",
                                  (workspace_id, kb_id)).fetchall()
            relations = db.execute("SELECT id, source_id, target_id, relation, properties FROM graph_relations WHERE workspace_id=? AND knowledge_base_id=?",
                                   (workspace_id, kb_id)).fetchall()
        return {
            "entities": [dict(id=row[0], name=row[1], entity_type=row[2], properties=json.loads(row[3])) for row in entities],
            "relations": [dict(id=row[0], source_id=row[1], target_id=row[2], relation=row[3], properties=json.loads(row[4])) for row in relations],
        }

    def add_relation(self, workspace_id: str, kb_id: str, source_id: str,
                     target_id: str, relation: str, properties: dict) -> dict:
        relation_id = str(uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO graph_relations VALUES (?, ?, ?, ?, ?, ?, ?)",
                       (relation_id, workspace_id, kb_id, source_id, target_id, relation,
                        json.dumps(properties, ensure_ascii=False)))
        return {"id": relation_id, "source_id": source_id, "target_id": target_id,
                "relation": relation, "properties": properties}

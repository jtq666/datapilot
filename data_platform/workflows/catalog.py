from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from uuid import uuid4

from .spec import WorkflowSpec


class WorkflowCatalog:
    """Workspace-scoped, immutable published workflow versions."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS workflows (
                    id TEXT PRIMARY KEY, workspace_id TEXT NOT NULL, name TEXT NOT NULL,
                    latest_version INTEGER NOT NULL DEFAULT 1,
                    UNIQUE(workspace_id, name)
                );
                CREATE TABLE IF NOT EXISTS workflow_versions (
                    workflow_id TEXT NOT NULL REFERENCES workflows(id),
                    version INTEGER NOT NULL, spec_json TEXT NOT NULL,
                    PRIMARY KEY(workflow_id, version)
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def create(self, workspace_id: str, name: str, spec: WorkflowSpec) -> str:
        spec.validate()
        if not workspace_id or not name.strip():
            raise ValueError("workspace and workflow name are required")
        workflow_id = str(uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO workflows VALUES (?, ?, ?, 1)", (workflow_id, workspace_id, name.strip()))
            db.execute("INSERT INTO workflow_versions VALUES (?, 1, ?)",
                       (workflow_id, json.dumps(spec.to_dict())))
        return workflow_id

    def get(self, workspace_id: str, workflow_id: str, version: int | None = None) -> tuple[int, WorkflowSpec]:
        with self._connect() as db:
            row = db.execute(
                "SELECT latest_version FROM workflows WHERE id=? AND workspace_id=?",
                (workflow_id, workspace_id),
            ).fetchone()
            if row is None:
                raise LookupError("workflow not found in workspace")
            selected = row[0] if version is None else version
            spec_row = db.execute(
                "SELECT spec_json FROM workflow_versions WHERE workflow_id=? AND version=?",
                (workflow_id, selected),
            ).fetchone()
            if spec_row is None:
                raise LookupError("workflow version not found")
        return selected, WorkflowSpec.from_dict(json.loads(spec_row[0]))

    def update(self, workspace_id: str, workflow_id: str, spec: WorkflowSpec, expected_version: int) -> int:
        spec.validate()
        with self._connect() as db:
            db.execute("BEGIN IMMEDIATE")
            changed = db.execute(
                "UPDATE workflows SET latest_version=latest_version+1 "
                "WHERE id=? AND workspace_id=? AND latest_version=?",
                (workflow_id, workspace_id, expected_version),
            ).rowcount
            if changed != 1:
                raise ValueError("workflow missing or version conflict")
            new_version = expected_version + 1
            db.execute("INSERT INTO workflow_versions VALUES (?, ?, ?)",
                       (workflow_id, new_version, json.dumps(spec.to_dict())))
        return new_version

    def list_for_workspace(self, workspace_id: str) -> list[dict[str, str | int]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT id, name, latest_version FROM workflows WHERE workspace_id=? ORDER BY name",
                (workspace_id,),
            ).fetchall()
        return [{"id": row[0], "name": row[1], "version": row[2]} for row in rows]


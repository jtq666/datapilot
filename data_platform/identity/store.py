from __future__ import annotations

import hashlib
import secrets
import sqlite3
import time
from pathlib import Path
from uuid import uuid4

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError


class IdentityStore:
    """Local development identity store; PostgreSQL migration is pending."""

    def __init__(self, path: str | Path):
        self.path = str(path)
        self.hasher = PasswordHasher()
        with self._connect() as db:
            db.executescript("""
                CREATE TABLE IF NOT EXISTS users (
                    id TEXT PRIMARY KEY, username TEXT NOT NULL UNIQUE COLLATE NOCASE,
                    password_hash TEXT NOT NULL
                );
                CREATE TABLE IF NOT EXISTS workspaces (
                    id TEXT PRIMARY KEY, name TEXT NOT NULL, owner_id TEXT NOT NULL REFERENCES users(id)
                );
                CREATE TABLE IF NOT EXISTS memberships (
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id),
                    user_id TEXT NOT NULL REFERENCES users(id),
                    role TEXT NOT NULL CHECK(role IN ('owner', 'member')),
                    PRIMARY KEY(workspace_id, user_id)
                );
                CREATE TABLE IF NOT EXISTS sessions (
                    token_hash TEXT PRIMARY KEY, user_id TEXT NOT NULL REFERENCES users(id),
                    expires_at INTEGER NOT NULL
                );
                CREATE TABLE IF NOT EXISTS data_sources (
                    workspace_id TEXT NOT NULL REFERENCES workspaces(id), name TEXT NOT NULL,
                    relative_path TEXT NOT NULL, PRIMARY KEY(workspace_id, name)
                );
            """)

    def _connect(self) -> sqlite3.Connection:
        db = sqlite3.connect(self.path)
        db.execute("PRAGMA foreign_keys=ON")
        return db

    def create_user(self, username: str, password: str) -> str:
        username = username.strip()
        if not 3 <= len(username) <= 64 or len(password) < 12:
            raise ValueError("username must be 3-64 characters and password at least 12")
        user_id = str(uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO users VALUES (?, ?, ?)", (user_id, username, self.hasher.hash(password)))
        return user_id

    def login(self, username: str, password: str, *, lifetime_seconds: int = 43200) -> str | None:
        with self._connect() as db:
            row = db.execute("SELECT id, password_hash FROM users WHERE username=?", (username,)).fetchone()
        if row is None:
            return None
        try:
            self.hasher.verify(row[1], password)
        except (VerifyMismatchError, InvalidHashError):
            return None
        token = secrets.token_urlsafe(32)
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as db:
            db.execute("INSERT INTO sessions VALUES (?, ?, ?)", (digest, row[0], int(time.time()) + lifetime_seconds))
        return token

    def user_for_token(self, token: str) -> str | None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as db:
            row = db.execute(
                "SELECT user_id FROM sessions WHERE token_hash=? AND expires_at>?",
                (digest, int(time.time())),
            ).fetchone()
        return None if row is None else row[0]

    def logout(self, token: str) -> None:
        digest = hashlib.sha256(token.encode()).hexdigest()
        with self._connect() as db:
            db.execute("DELETE FROM sessions WHERE token_hash=?", (digest,))

    def create_workspace(self, owner_id: str, name: str) -> str:
        if not name.strip():
            raise ValueError("workspace name is required")
        workspace_id = str(uuid4())
        with self._connect() as db:
            db.execute("INSERT INTO workspaces VALUES (?, ?, ?)", (workspace_id, name.strip(), owner_id))
            db.execute("INSERT INTO memberships VALUES (?, ?, 'owner')", (workspace_id, owner_id))
        return workspace_id

    def role(self, user_id: str, workspace_id: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT role FROM memberships WHERE user_id=? AND workspace_id=?",
                (user_id, workspace_id),
            ).fetchone()
        return None if row is None else row[0]

    def list_workspaces(self, user_id: str) -> list[dict[str, str]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT w.id, w.name, m.role FROM workspaces w "
                "JOIN memberships m ON m.workspace_id=w.id WHERE m.user_id=? ORDER BY w.name",
                (user_id,),
            ).fetchall()
        return [{"id": row[0], "name": row[1], "role": row[2]} for row in rows]

    def add_member(self, owner_id: str, workspace_id: str, user_id: str) -> None:
        if self.role(owner_id, workspace_id) != "owner":
            raise PermissionError("owner role required")
        with self._connect() as db:
            db.execute(
                "INSERT INTO memberships VALUES (?, ?, 'member')",
                (workspace_id, user_id),
            )

    def register_source(self, workspace_id: str, name: str, relative_path: str) -> None:
        with self._connect() as db:
            db.execute(
                "INSERT INTO data_sources VALUES (?, ?, ?) "
                "ON CONFLICT(workspace_id, name) DO UPDATE SET relative_path=excluded.relative_path",
                (workspace_id, name, relative_path),
            )

    def source_path(self, workspace_id: str, name: str) -> str | None:
        with self._connect() as db:
            row = db.execute(
                "SELECT relative_path FROM data_sources WHERE workspace_id=? AND name=?",
                (workspace_id, name),
            ).fetchone()
        return None if row is None else row[0]

    def list_sources(self, workspace_id: str) -> list[dict[str, str]]:
        with self._connect() as db:
            rows = db.execute(
                "SELECT name, relative_path FROM data_sources WHERE workspace_id=? ORDER BY name",
                (workspace_id,),
            ).fetchall()
        return [{"name": row[0], "relative_path": row[1]} for row in rows]

    def delete_source(self, workspace_id: str, name: str) -> bool:
        with self._connect() as db:
            result = db.execute(
                "DELETE FROM data_sources WHERE workspace_id=? AND name=?",
                (workspace_id, name),
            )
        return result.rowcount == 1


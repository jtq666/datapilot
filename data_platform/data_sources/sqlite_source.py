from __future__ import annotations

import sqlite3
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import sqlglot
from sqlglot import exp
from sqlglot.errors import ParseError


class QueryRejected(ValueError):
    """A query violates the read-only query contract."""


class QueryExecutionError(RuntimeError):
    """A valid query failed at the database layer."""


@dataclass(frozen=True)
class QueryResult:
    columns: list[str]
    rows: list[list[Any]]
    truncated: bool
    elapsed_ms: int


READ_ROOTS = (exp.Select, exp.Union, exp.Intersect, exp.Except)
FORBIDDEN_NODES = (
    exp.DML, exp.DDL, exp.Command, exp.Pragma, exp.Attach, exp.Detach,
    exp.Transaction, exp.Copy, exp.Set, exp.Use, exp.Into, exp.LoadData,
)
ALLOWED_FUNCTIONS = {
    "abs", "avg", "coalesce", "count", "date", "datetime", "ifnull", "length",
    "lower", "max", "min", "nullif", "round", "strftime", "substr", "sum",
    "total", "trim", "upper",
}


def validate_read_query(sql: str) -> None:
    if not sql or len(sql) > 10_000:
        raise QueryRejected("SQL must be nonempty and at most 10000 characters")
    try:
        statements = [statement for statement in sqlglot.parse(sql, read="sqlite") if statement]
    except ParseError as exc:
        raise QueryRejected("SQL syntax could not be parsed") from exc
    if len(statements) != 1 or not isinstance(statements[0], READ_ROOTS):
        raise QueryRejected("Only one read-only SELECT query is allowed")
    if any(isinstance(node, FORBIDDEN_NODES) for node in statements[0].walk()):
        raise QueryRejected("Query contains a forbidden operation")
    # SQLite read-only file mode is the final boundary. Parsing is an early filter,
    # not a complete SQL security proof.


class SQLiteDataSource:
    def __init__(self, path: str | Path, *, max_rows: int = 100, timeout_seconds: float = 3.0):
        self.path = Path(path).resolve(strict=True)
        if not self.path.is_file():
            raise ValueError("database path must be a file")
        if not 1 <= max_rows <= 1000:
            raise ValueError("max_rows must be between 1 and 1000")
        if not 0 < timeout_seconds <= 30:
            raise ValueError("timeout_seconds must be between 0 and 30")
        self.max_rows = max_rows
        self.timeout_seconds = timeout_seconds

    def _connect(self) -> sqlite3.Connection:
        # A URI opened in mode=ro cannot write even if a model bypasses parsing.
        db = sqlite3.connect(f"{self.path.as_uri()}?mode=ro", uri=True, timeout=self.timeout_seconds)
        db.execute("PRAGMA query_only = ON")
        return db

    def schema(self) -> list[dict[str, Any]]:
        tables: list[dict[str, Any]] = []
        with self._connect() as db:
            names = db.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' "
                "ORDER BY name LIMIT 100"
            ).fetchall()
            for (name,) in names:
                quoted = '"' + name.replace('"', '""') + '"'
                columns = [
                    {"name": row[1], "type": row[2], "nullable": not bool(row[3]), "primary_key": bool(row[5])}
                    for row in db.execute(f"PRAGMA table_info({quoted})").fetchall()
                ]
                tables.append({"table": name, "columns": columns})
        return tables

    def query(self, sql: str) -> QueryResult:
        validate_read_query(sql)
        started = time.monotonic()
        deadline = started + self.timeout_seconds
        try:
            with self._connect() as db:
                db.set_authorizer(self._authorize)
                db.set_progress_handler(lambda: 1 if time.monotonic() > deadline else 0, 500)
                cursor = db.execute(sql)
                columns = [item[0] for item in cursor.description or []]
                rows = cursor.fetchmany(self.max_rows + 1)
                truncated = len(rows) > self.max_rows
                rows = rows[:self.max_rows]
        except sqlite3.Error as exc:
            # Avoid returning SQL text, database paths or credentials to the model.
            if time.monotonic() > deadline:
                raise QueryExecutionError("query_timeout") from exc
            raise QueryExecutionError("query_failed") from exc
        return QueryResult(columns, [list(row) for row in rows], truncated, int((time.monotonic() - started) * 1000))

    @staticmethod
    def _authorize(action: int, arg1: str | None, arg2: str | None, *_: Any) -> int:
        if action in (sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ):
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_FUNCTION and (arg2 or arg1 or "").lower() in ALLOWED_FUNCTIONS:
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY


class DataSourceRegistry:
    """A trusted administrator registers files; an Agent only receives names."""

    def __init__(self, root: str | Path):
        self.root = Path(root).resolve(strict=True)
        self._sources: dict[tuple[str, str], SQLiteDataSource] = {}

    def register(self, workspace_id: str, name: str, path: str | Path) -> None:
        if not workspace_id or not name:
            raise ValueError("workspace and source name are required")
        resolved = Path(path).resolve(strict=True)
        if not resolved.is_relative_to(self.root):
            raise ValueError("database must stay within configured data root")
        self._sources[(workspace_id, name)] = SQLiteDataSource(resolved)

    def get(self, workspace_id: str, name: str) -> SQLiteDataSource:
        try:
            return self._sources[(workspace_id, name)]
        except KeyError as exc:
            raise LookupError("data source not found in workspace") from exc


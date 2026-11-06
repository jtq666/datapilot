from __future__ import annotations

from dataclasses import asdict

from data_platform.agents.tools import ToolDefinition, ToolExecutionError, ToolRegistry

from .sqlite_source import QueryExecutionError, QueryRejected, SQLiteDataSource


def create_sqlite_tools(source: SQLiteDataSource) -> ToolRegistry:
    """Bind a trusted source to tools; model arguments cannot supply a file path."""
    tools = ToolRegistry()
    def query(args):
        try:
            return asdict(source.query(args["sql"]))
        except QueryRejected as exc:
            raise ToolExecutionError("query_rejected", str(exc)) from exc
        except QueryExecutionError as exc:
            raise ToolExecutionError(str(exc), "Database could not execute the read query") from exc

    tools.register(ToolDefinition(
        "database_schema", "List tables and their columns in the selected database",
        {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _: source.schema(),
    ))
    tools.register(ToolDefinition(
        "query_database", "Run one bounded, read-only SQLite SELECT query",
        {"type": "object", "properties": {"sql": {"type": "string"}},
         "required": ["sql"], "additionalProperties": False},
        query,
    ))
    return tools


import json
from contextlib import closing

import psycopg
from sqlglot import exp, parse
from sqlglot.errors import SqlglotError
from sqlglot.optimizer.scope import Scope, traverse_scope

from .tools import ToolDefinition, ToolExecutionError, ToolRegistry


TABLES = {"orders", "refunds"}
FUNCTIONS = {
    "ABS", "AVG", "CASE", "CAST", "COALESCE", "COUNT", "CURRENT_DATE", "DATE_TRUNC",
    "DENSE_RANK", "EXTRACT", "GREATEST", "IF", "LEAST", "LOWER", "MAX", "MIN",
    "NULLIF", "RANK", "ROUND", "ROW_NUMBER", "SUM", "TIMESTAMP_TRUNC", "TIME_TO_STR", "TRIM", "UPPER",
}
FORBIDDEN = (exp.DML, exp.DDL, exp.Into, exp.Lock, exp.Command, exp.Placeholder, exp.Parameter)
MAX_ROWS = 100


def validated_sql(sql: str) -> str:
    try:
        statements = [part for part in parse(sql, read="postgres") if part is not None]
    except (SqlglotError, RecursionError) as exc:
        raise ToolExecutionError("invalid_sql", "Check PostgreSQL syntax") from exc
    if len(statements) != 1 or not isinstance(statements[0], (exp.Select, exp.SetOperation)):
        raise ToolExecutionError("readonly_required", "A single SELECT is required")
    query = statements[0]
    if any(isinstance(node, FORBIDDEN) for node in query.walk()):
        raise ToolExecutionError("readonly_required", "Only read-only SELECT is allowed")
    for scope in traverse_scope(query):
        for table, source in scope.selected_sources.values():
            if not isinstance(table, exp.Table):
                continue
            if not isinstance(table.this, exp.Identifier) or table.catalog:
                raise ToolExecutionError("unauthorized_table", "Use an authorized analytics table")
            if isinstance(source, Scope) and not table.db:
                continue
            if table.db not in ("", "analytics") or table.name not in TABLES:
                raise ToolExecutionError("unauthorized_table", "Use an authorized analytics table")
    for function in query.find_all(exp.Func):
        if isinstance(function, exp.Connector):
            continue
        name = function.name if isinstance(function, exp.Anonymous) else function.sql_name()
        if isinstance(function.parent, exp.Dot) or name.upper() not in FUNCTIONS:
            raise ToolExecutionError("unsupported_function",
                                     "Unsupported SQL expression. Use simple CASE, CAST, COUNT, SUM or DATE_TRUNC.")
    try:
        return query.sql(dialect="postgres", comments=False, unsupported_level="RAISE")
    except SqlglotError as exc:
        raise ToolExecutionError("invalid_sql", "Unsupported PostgreSQL syntax") from exc


class BusinessSource:
    def __init__(self, dsn: str):
        self.dsn = dsn

    def schema(self):
        with psycopg.connect(self.dsn, connect_timeout=4) as conn:
            rows = conn.execute("""
                SELECT table_name, column_name, data_type FROM information_schema.columns
                WHERE table_schema = 'analytics' AND table_name = ANY(%s)
                ORDER BY table_name, ordinal_position
            """, (sorted(TABLES),)).fetchall()
        tables = {}
        for table, column, data_type in rows:
            tables.setdefault(table, []).append({"name": column, "type": data_type})
        return tables

    def query(self, sql: str):
        sql = validated_sql(sql)
        try:
            with psycopg.connect(self.dsn, connect_timeout=4) as conn:
                conn.execute("SET TRANSACTION READ ONLY")
                conn.execute("SET LOCAL statement_timeout = '3s'")
                conn.execute("SET LOCAL search_path TO pg_catalog, analytics")
                cursor = conn.execute(f"SELECT * FROM ({sql}) AS agent_result LIMIT {MAX_ROWS + 1}")
                rows = cursor.fetchall()
                return {
                    "sql": sql,
                    "columns": [column.name for column in cursor.description],
                    "rows": json.loads(json.dumps(rows[:MAX_ROWS], default=str)),
                    "truncated": len(rows) > MAX_ROWS,
                }
        except psycopg.Error as exc:
            code, message = {
                "42703": ("unknown_column", "Column not found. Read database_schema and correct the SQL."),
                "42P01": ("unknown_table", "Table not found. Read database_schema and correct the SQL."),
                "42601": ("invalid_sql", "Check PostgreSQL syntax."),
                "42501": ("permission_denied", "Database denied access."),
                "57014": ("query_timeout", "Query exceeded the time limit."),
            }.get(exc.sqlstate, ("query_failed", "Database could not execute the query."))
            raise ToolExecutionError(code, message) from exc

    def tools(self):
        registry = ToolRegistry()
        registry.register(ToolDefinition(
            "database_schema", "Read authorized PostgreSQL table names and columns.",
            {"type": "object", "properties": {}, "additionalProperties": False},
            lambda _: self.schema(),
        ))
        registry.register(ToolDefinition(
            "query_database", "Execute a single read-only PostgreSQL SELECT. Check schema if uncertain.",
            {"type": "object", "properties": {"sql": {"type": "string", "minLength": 1, "maxLength": 8000}},
             "required": ["sql"], "additionalProperties": False},
            lambda args: self.query(args["sql"]),
        ))
        return registry

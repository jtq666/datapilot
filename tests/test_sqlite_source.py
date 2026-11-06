import sqlite3

import pytest

from data_platform.agents.contracts import ModelReply, ToolCall
from data_platform.agents.runner import AgentRunner
from data_platform.data_sources.agent_tools import create_sqlite_tools
from data_platform.data_sources.sqlite_source import DataSourceRegistry, QueryRejected, SQLiteDataSource
from data_platform.runtime.models import RunStatus
from data_platform.runtime.store import RunStore


@pytest.fixture
def sample_db(tmp_path):
    path = tmp_path / "sales.db"
    with sqlite3.connect(path) as db:
        db.executescript("""
            CREATE TABLE customers(id INTEGER PRIMARY KEY, name TEXT);
            CREATE TABLE orders(id INTEGER PRIMARY KEY, customer_id INTEGER, amount INTEGER);
            INSERT INTO customers VALUES(1, 'A'), (2, 'B');
            INSERT INTO orders VALUES(1, 1, 20), (2, 1, 30), (3, 2, 7);
        """)
    return path


def test_schema_and_join_query(sample_db):
    source = SQLiteDataSource(sample_db)
    schema = source.schema()
    assert [entry["table"] for entry in schema] == ["customers", "orders"]
    result = source.query("SELECT c.name, SUM(o.amount) AS revenue FROM customers c JOIN orders o ON o.customer_id=c.id GROUP BY c.name ORDER BY revenue DESC")
    assert result.rows == [["A", 50], ["B", 7]]
    assert result.truncated is False


@pytest.mark.parametrize("sql", [
    "DELETE FROM orders", "DROP TABLE orders", "SELECT * FROM orders; DELETE FROM orders",
    "WITH x AS (DELETE FROM orders) SELECT * FROM x", "PRAGMA database_list", "ATTACH DATABASE 'x' AS other",
])
def test_write_or_admin_sql_rejected(sample_db, sql):
    with pytest.raises(QueryRejected):
        SQLiteDataSource(sample_db).query(sql)


def test_row_limit_and_workspace_isolation(sample_db, tmp_path):
    source = SQLiteDataSource(sample_db, max_rows=2)
    result = source.query("SELECT * FROM orders ORDER BY id")
    assert len(result.rows) == 2
    assert result.truncated is True
    registry = DataSourceRegistry(tmp_path)
    registry.register("team-a", "sales", sample_db)
    with pytest.raises(LookupError):
        registry.get("team-b", "sales")
    outside = tmp_path.parent / "outside.db"
    with sqlite3.connect(outside) as db:
        db.execute("CREATE TABLE t(x)")
    with pytest.raises(ValueError, match="data root"):
        registry.register("team-a", "outside", outside)


def test_agent_sql_tool_in_actual_loop(sample_db, tmp_path):
    class ScriptedModel:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                assert {tool["function"]["name"] for tool in tools} == {"database_schema", "query_database"}
                return ModelReply(tool_calls=(ToolCall("query-1", "query_database", {"sql": "SELECT COUNT(*) AS n FROM customers"}),))
            assert '"rows": [[2]]' in messages[-1]["content"]
            return ModelReply(content="共有 2 位客户")

    store = RunStore(tmp_path / "runs.db")
    run_id = AgentRunner(store, ScriptedModel(), create_sqlite_tools(SQLiteDataSource(sample_db))).start("team-a", "多少客户？")
    assert store.get_run(run_id, "team-a").status == RunStatus.SUCCEEDED
    assert store.load_checkpoint(run_id, "team-a")[1]["answer"] == "共有 2 位客户"


def test_query_tool_returns_safe_structured_error(sample_db):
    tools = create_sqlite_tools(SQLiteDataSource(sample_db))
    result = tools.execute("query_database", {"sql": "DELETE FROM orders"})
    assert result.ok is False
    assert result.error_code == "query_rejected"
    assert "DELETE" not in (result.message or "")
    result = tools.execute("query_database", {"sql": "SELECT * FROM missing_table"})
    assert result.ok is False
    assert result.error_code == "query_failed"

import os
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import urlsplit
from contextlib import contextmanager
from uuid import uuid4

import pytest
from argon2 import PasswordHasher
from fastapi.testclient import TestClient
from langgraph.checkpoint.postgres import PostgresSaver

from datapilot.api import create_app
from datapilot.knowledge import Knowledge
from datapilot.sql import BusinessSource
from datapilot.store import Store
from datapilot.tools import ToolExecutionError
from test_agent import call


pytestmark = pytest.mark.postgres
SQL = """SELECT o.channel, COUNT(DISTINCT o.order_id) AS paid,
COUNT(DISTINCT r.order_id) AS refunded
FROM orders o LEFT JOIN refunds r ON o.order_id = r.order_id AND r.status='succeeded'
WHERE o.paid_at >= date_trunc('month', CURRENT_DATE)
GROUP BY o.channel ORDER BY o.channel"""


class Embeddings:
    def passages(self, texts):
        return [[1.0] + [0.0] * 1023 for _ in texts]

    queries = passages


class Model:
    calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if messages[-1]["role"] == "user":
            return call("sql", "query_database", {"sql": SQL})
        return {"role": "assistant", "content": "app 25%，web 25%，见 Q1。"}


def databases():
    app = os.getenv("APP_DATABASE_URL")
    business = os.getenv("BUSINESS_DATABASE_URL")
    if not app or not business:
        pytest.skip("Requires initialized PostgreSQL databases")
    return app, business


def test_real_postgres_readonly_source():
    _, business = databases()
    source = BusinessSource(business)
    assert set(source.schema()) == {"orders", "refunds"}
    assert source.query(SQL)["rows"] == [["app", 4, 1], ["web", 4, 1]]
    big = source.query("SELECT a.order_id FROM orders a CROSS JOIN orders b CROSS JOIN orders c")
    assert len(big["rows"]) == 100 and big["truncated"]
    with pytest.raises(ToolExecutionError) as exc:
        source.query("SELECT missing_column FROM orders")
    assert exc.value.code == "unknown_column"
    with pytest.raises(ToolExecutionError):
        source.query("SELECT phone FROM analytics.customer_contacts")
    with source_tools_connection(business) as conn:
        import psycopg
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT phone FROM analytics.customer_contacts")


@contextmanager
def source_tools_connection(dsn):
    import psycopg
    with psycopg.connect(dsn) as conn:
        yield conn
        conn.rollback()


def test_approval_survives_new_graph_and_uses_one_share():
    app_dsn, business_dsn = databases()
    store, source, model = Store(app_dsn), BusinessSource(business_dsn), Model()
    store.setup()
    analyst, reviewer = "test-owner-" + uuid4().hex, "test-review-" + uuid4().hex
    hashed = PasswordHasher().hash("integration-pass")
    store.seed_user(analyst, hashed, True)
    store.seed_user(reviewer, hashed, False)
    knowledge = Knowledge(store, Embeddings())
    rule_key = "test-" + uuid4().hex
    knowledge.publish("demo", "orders", rule_key, "Refund rule", "v1: count requested refunds.")
    knowledge.publish("demo", "orders", rule_key, "Refund rule", "v2: count successful refunds.")
    try:
        with PostgresSaver.from_conn_string(app_dsn) as saver:
            saver.setup()
            with TestClient(create_app(store, source, knowledge, model, saver)) as client:
                token = client.post("/auth/login", json={"username": analyst, "password": "integration-pass"}).json()["token"]
                headers = {"Authorization": "Bearer " + token}
                assert client.post("/auth/demo", json={"role": "analyst"}).status_code == 404
                result = client.post("/runs/start", headers=headers, json={
                    "question": "Refund rate per channel", "mode": "report", "recipient": reviewer,
                    "rule_key": rule_key,
                })
                assert result.status_code == 202 and result.json()["status"] == "running"
                pending = client.get(f"/runs/{result.json()['id']}", headers=headers).json()
                assert pending["status"] == "waiting", pending
                assert pending["rule"][0]["version"] == 2
                run_id = pending["id"]
                threads = client.get("/conversations", headers=headers).json()
                assert threads[0]["id"] == pending["conversation"]
                turns = client.get(f"/conversations/{pending['conversation']}", headers=headers).json()
                assert [row["id"] for row in turns] == [run_id]
                assert client.get(f"/knowledge/{rule_key}?version=1", headers=headers).json()["version"] == 1
                assert client.get(f"/knowledge/{rule_key}", headers=headers).json()["version"] == 2
                assert set(client.get("/sources/orders/schema", headers=headers).json()) == {"orders","refunds"}
                extra = {"question": "Refund rate per channel", "mode": "report",
                         "recipient": reviewer, "rule_key": rule_key}
                rejected = client.post("/runs", headers=headers, json=extra).json()
                refused = client.post(f"/runs/{rejected['id']}/approval", headers=headers, json={"approved": False})
                assert refused.json()["status"] == "rejected"
                expired = client.post("/runs", headers=headers, json=extra).json()
                with store.connect() as conn:
                    conn.execute("UPDATE runs SET deadline=now()-interval '1 second' WHERE id=%s", (expired["id"],))
                assert client.get(f"/runs/{expired['id']}", headers=headers).json()["status"] == "expired"
                assert client.post(f"/runs/{expired['id']}/approval", headers=headers, json={"approved": True}).status_code == 409
        assert model.calls == 6
        with PostgresSaver.from_conn_string(app_dsn) as restarted:
            with TestClient(create_app(store, source, knowledge, model, restarted)) as client:
                url = f"/runs/{run_id}/approval"
                reviewer_token = client.post("/auth/login", json={"username": reviewer, "password": "integration-pass"}).json()["token"]
                reviewer_headers = {"Authorization": "Bearer " + reviewer_token}
                assert client.post(url, headers=reviewer_headers, json={"approved": True}).status_code == 404
                assert client.get(f"/conversations/{pending['conversation']}", headers=reviewer_headers).status_code == 404
                def approve(_):
                    return client.post(url, headers=headers, json={"approved": True})
                with ThreadPoolExecutor(max_workers=2) as pool:
                    concurrent = list(pool.map(approve, range(2)))
                assert {r.status_code for r in concurrent} <= {200, 409}
                assert any(r.status_code == 200 and r.json()["status"] == "shared" for r in concurrent)
                assert client.post(url, headers=headers, json={"approved": True}).json()["status"] == "shared"
                assert client.post(url, headers=headers, json={"approved": False}).status_code == 409
                assert client.get(f"/shared/{run_id}", headers=headers).status_code == 404
                view = client.get(f"/shared/{run_id}", headers=reviewer_headers)
                assert view.json()["evidence"][0]["rows"] == [["app", 4, 1], ["web", 4, 1]]
        assert model.calls == 6
        with store.connect() as conn:
            assert conn.execute("SELECT count(*) FROM shares WHERE run=%s", (run_id,)).fetchone()[0] == 1
            assert conn.execute("SELECT count(*) FROM shares WHERE run IN (%s,%s)",
                                (rejected["id"], expired["id"])).fetchone()[0] == 0
    finally:
        # These accounts and rules are uniquely generated for this test.
        with store.connect() as conn:
            conn.execute("DELETE FROM sessions WHERE username IN (%s,%s)", (analyst, reviewer))
            conn.execute("DELETE FROM members WHERE username IN (%s,%s)", (analyst, reviewer))
            conn.execute("UPDATE knowledge_documents SET active=false WHERE doc_key=%s", (rule_key,))


def test_server_enforces_query_timeout():
    _, business = databases()
    admin_password = os.getenv("POSTGRES_ADMIN_PASSWORD")
    if not admin_password:
        pytest.skip("Requires demo database admin to hold a test lock")
    import psycopg
    connection = urlsplit(business)
    with psycopg.connect(host=connection.hostname, port=connection.port,
                         dbname=connection.path.lstrip("/"), user="postgres",
                         password=admin_password, connect_timeout=4) as conn:
        conn.execute("LOCK TABLE analytics.orders IN ACCESS EXCLUSIVE MODE")
        with pytest.raises(ToolExecutionError) as exc:
            BusinessSource(business).query("SELECT COUNT(*) FROM orders")
        assert exc.value.code == "query_timeout"

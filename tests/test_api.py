import sqlite3

from fastapi.testclient import TestClient

from data_platform.agents.contracts import ModelReply, ToolCall
from data_platform.api.app import create_app


class ScriptedModel:
    def __init__(self):
        self.calls = 0

    def complete(self, messages, tools):
        self.calls += 1
        if self.calls == 1:
            return ModelReply(tool_calls=(ToolCall("call-1", "query_database", {"sql": "SELECT COUNT(*) AS n FROM customers"}),))
        return ModelReply(content="共有 2 位客户")


def test_authenticated_database_and_agent_flow(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    db_path = data / "customers.db"
    with sqlite3.connect(db_path) as db:
        db.executescript("CREATE TABLE customers(id INTEGER); INSERT INTO customers VALUES(1), (2);")
    app = create_app(data, tmp_path / "state", model_factory=ScriptedModel)
    identity = app.state.identity
    identity.create_user("alice", "correct-long-password")
    identity.create_user("bob", "another-long-password")
    client = TestClient(app)
    assert client.get("/v1/health").json() == {"status": "ok"}
    assert client.get("/v1/workspaces/w/runs/r").status_code == 401
    token = client.post("/v1/auth/login", json={"username": "alice", "password": "correct-long-password"}).json()["access_token"]
    bob = client.post("/v1/auth/login", json={"username": "bob", "password": "another-long-password"}).json()["access_token"]
    alice_headers = {"Authorization": f"Bearer {token}"}
    bob_headers = {"Authorization": f"Bearer {bob}"}
    workspace = client.post("/v1/workspaces", json={"name": "Sales"}, headers=alice_headers).json()["id"]
    assert client.get("/v1/workspaces", headers=alice_headers).json()["workspaces"][0]["id"] == workspace
    assert client.get("/v1/workspaces", headers=bob_headers).json()["workspaces"] == []
    source_url = f"/v1/workspaces/{workspace}/sources/customers"
    assert client.post(source_url, json={"relative_path": "customers.db"}, headers=bob_headers).status_code == 404
    assert client.post(source_url, json={"relative_path": "../outside.db"}, headers=alice_headers).status_code == 400
    assert client.post(source_url, json={"relative_path": "customers.db"}, headers=alice_headers).status_code == 201
    assert client.get(f"/v1/workspaces/{workspace}/sources", headers=alice_headers).json()["sources"][0]["name"] == "customers"
    assert client.get(source_url + "/schema", headers=alice_headers).json()["tables"][0]["table"] == "customers"
    assert client.post(source_url + "/query", json={"sql": "DELETE FROM customers"}, headers=alice_headers).status_code == 400
    assert client.post(source_url + "/query", json={"sql": "SELECT COUNT(*) FROM customers"}, headers=alice_headers).json()["rows"] == [[2]]
    assert client.post(source_url + "/ask", json={"question": "有多少客户？", "protocol": "react"},
                       headers=alice_headers).status_code == 422
    response = client.post(source_url + "/ask", json={"question": "有多少客户？"}, headers=alice_headers)
    assert response.status_code == 200, response.text
    assert response.json()["answer"] == "共有 2 位客户"
    run_id = response.json()["run_id"]
    assert any(run["id"] == run_id for run in client.get(
        f"/v1/workspaces/{workspace}/runs", headers=alice_headers
    ).json()["runs"])
    assert client.get(f"/v1/workspaces/{workspace}/runs/{run_id}", headers=bob_headers).status_code == 404
    assert len(client.get(f"/v1/workspaces/{workspace}/runs/{run_id}/events", headers=alice_headers).json()["events"]) == 4
    assert client.post("/v1/auth/logout", headers=alice_headers).status_code == 200
    assert client.get(source_url + "/schema", headers=alice_headers).status_code == 401


def test_knowledge_and_sql_tools_share_one_agent_run(tmp_path):
    class FixedEmbeddings:
        def passages(self, texts):
            return [[1.0, 0.0] for _ in texts]

        def queries(self, texts):
            return [[1.0, 0.0] for _ in texts]

    class CombinedModel:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 1:
                assert {item["function"]["name"] for item in tools} == {
                    "database_schema", "query_database", "search_knowledge"
                }
                return ModelReply(tool_calls=(
                    ToolCall("sql-1", "query_database", {"sql": "SELECT COUNT(*) FROM customers"}),
                    ToolCall("rag-1", "search_knowledge", {"query": "退款规则"}),
                ))
            assert '"rows": [[2]]' in messages[-2]["content"]
            assert '"filename": "refund.md"' in messages[-1]["content"]
            return ModelReply(content="共有 2 人。退款规则见 refund.md 第 0 段。")

    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "customers.db") as db:
        db.executescript("CREATE TABLE customers(id INTEGER); INSERT INTO customers VALUES(1), (2);")
    app = create_app(data, tmp_path / "state", model_factory=CombinedModel, embedding_factory=FixedEmbeddings)
    app.state.identity.create_user("alice", "correct-long-password")
    client = TestClient(app)
    token = client.post("/v1/auth/login", json={"username": "alice", "password": "correct-long-password"}).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    workspace = client.post("/v1/workspaces", json={"name": "Sales"}, headers=headers).json()["id"]
    source_url = f"/v1/workspaces/{workspace}/sources/customers"
    client.post(source_url, json={"relative_path": "customers.db"}, headers=headers)
    kb_url = f"/v1/workspaces/{workspace}/knowledge-bases"
    kb = client.post(kb_url, json={"name": "Policy"}, headers=headers).json()["id"]
    assert client.get(kb_url, headers=headers).json()["knowledge_bases"][0]["id"] == kb
    response = client.post(f"{kb_url}/{kb}/documents", json={
        "filename": "refund.md", "text": "退款规则：七天内可退款。",
    }, headers=headers)
    assert response.status_code == 201, response.text
    hits = client.get(f"{kb_url}/{kb}/search", params={"q": "退款规则"}, headers=headers).json()["hits"]
    assert hits[0]["filename"] == "refund.md"
    answer = client.post(source_url + "/ask", json={
        "question": "多少客户？退款规则是什么？", "knowledge_base_id": kb,
    }, headers=headers)
    assert answer.status_code == 200, answer.text
    assert "refund.md" in answer.json()["answer"]


def test_workflow_api_versions_and_human_approval(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    app = create_app(data, tmp_path / "state")
    app.state.identity.create_user("alice", "correct-long-password")
    app.state.identity.create_user("bob", "another-long-password")
    client = TestClient(app)
    alice = client.post("/v1/auth/login", json={
        "username": "alice", "password": "correct-long-password"
    }).json()["access_token"]
    bob = client.post("/v1/auth/login", json={
        "username": "bob", "password": "another-long-password"
    }).json()["access_token"]
    alice_headers = {"Authorization": f"Bearer {alice}"}
    bob_headers = {"Authorization": f"Bearer {bob}"}
    workspace = client.post("/v1/workspaces", json={"name": "Research"}, headers=alice_headers).json()["id"]
    base = f"/v1/workspaces/{workspace}/workflows"
    spec = {
        "start": "approve",
        "nodes": [
            {"id": "approve", "kind": "approval", "config": {"output_key": "ok"}},
            {"id": "yes", "kind": "end", "config": {"result_key": "ok"}},
            {"id": "no", "kind": "end", "config": {"result_key": "ok"}},
        ],
        "edges": [
            {"source": "approve", "target": "yes", "label": "true"},
            {"source": "approve", "target": "no", "label": "false"},
        ],
    }
    created = client.post(base, json={"name": "review", "spec": spec}, headers=alice_headers)
    assert created.status_code == 201, created.text
    workflow_id = created.json()["id"]
    assert client.get(f"{base}/{workflow_id}", headers=bob_headers).status_code == 404
    started = client.post(f"{base}/{workflow_id}/runs", json={"inputs": {}}, headers=alice_headers)
    assert started.status_code == 200, started.text
    assert started.json()["status"] == "waiting"
    run_id = started.json()["run_id"]
    detail = client.get(f"/v1/workspaces/{workspace}/runs/{run_id}", headers=alice_headers)
    assert detail.json()["workflow_id"] == workflow_id
    resume_url = f"{base}/{workflow_id}/runs/{run_id}/resume"
    assert client.post(resume_url, json={"approval": True}, headers=bob_headers).status_code == 404
    resumed = client.post(resume_url, json={"approval": True}, headers=alice_headers)
    assert resumed.status_code == 200, resumed.text
    assert resumed.json()["status"] == "succeeded"
    assert resumed.json()["answer"] is True
    updated = client.put(f"{base}/{workflow_id}", json={"expected_version": 1, "spec": spec}, headers=alice_headers)
    assert updated.json()["version"] == 2
    assert client.get(f"{base}/{workflow_id}?version=1", headers=alice_headers).json()["version"] == 1


def test_workflow_api_uses_registered_sql_tool(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "sales.db") as db:
        db.executescript("CREATE TABLE sales(amount INTEGER); INSERT INTO sales VALUES(20), (30);")
    app = create_app(data, tmp_path / "state")
    app.state.identity.create_user("alice", "correct-long-password")
    client = TestClient(app)
    token = client.post("/v1/auth/login", json={
        "username": "alice", "password": "correct-long-password"
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    workspace = client.post("/v1/workspaces", json={"name": "Sales"}, headers=headers).json()["id"]
    client.post(f"/v1/workspaces/{workspace}/sources/sales", json={"relative_path": "sales.db"}, headers=headers)
    spec = {
        "start": "query",
        "nodes": [
            {"id": "query", "kind": "tool", "config": {
                "name": "query_database", "arguments": {"sql": "SELECT SUM(amount) AS total FROM sales"},
                "output_key": "result",
            }},
            {"id": "done", "kind": "end", "config": {"result_key": "result"}},
        ],
        "edges": [{"source": "query", "target": "done"}],
    }
    base = f"/v1/workspaces/{workspace}/workflows"
    workflow = client.post(base, json={"name": "daily sales", "spec": spec}, headers=headers).json()["id"]
    response = client.post(f"{base}/{workflow}/runs", json={
        "inputs": {}, "source_name": "sales"
    }, headers=headers)
    assert response.status_code == 200, response.text
    assert response.json()["answer"]["rows"] == [[50]]

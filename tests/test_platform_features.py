import sqlite3

from fastapi.testclient import TestClient

from data_platform.api.app import create_app


def _client(tmp_path):
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "sales.db") as db:
        db.execute("CREATE TABLE sales(amount INTEGER)")
        db.executemany("INSERT INTO sales VALUES (?)", [(10,), (20,)])
    app = create_app(data, tmp_path / "state")
    app.state.identity.create_user("alice", "correct-long-password")
    client = TestClient(app)
    token = client.post("/v1/auth/login", json={
        "username": "alice", "password": "correct-long-password",
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    workspace = client.post("/v1/workspaces", json={"name": "Sales"}, headers=headers).json()["id"]
    return client, headers, workspace


def test_resource_catalog_share_file_prompt_and_evaluation(tmp_path):
    client, headers, workspace = _client(tmp_path)
    base = f"/v1/workspaces/{workspace}"

    model = client.post(base + "/models", json={
        "name": "local", "config": {"provider": "ollama", "model": "qwen"},
    }, headers=headers)
    assert model.status_code == 201, model.text
    assert client.get(base + "/models", headers=headers).json()["models"][0]["name"] == "local"
    assert client.get(base + f"/models/{model.json()['id']}/health", headers=headers).json()["status"] == "ready"

    prompt = client.post(base + "/prompts", json={
        "name": "answer", "config": {"template": "Hello {name}"},
    }, headers=headers).json()
    rendered = client.get(base + f"/prompts/{prompt['id']}/render?variables={{\"name\":\"Ada\"}}",
                          headers=headers)
    assert rendered.json()["text"] == "Hello Ada"

    client.app.state.runs.create_run("run-1", workspace, "test")
    share = client.post(base + "/shares", json={
        "resource_type": "run", "resource_id": "run-1", "ttl_seconds": 60,
    }, headers=headers).json()
    assert client.get("/v1/shares/" + share["token"]).status_code == 200
    assert client.delete(base + "/shares/" + share["token"], headers=headers).status_code == 200
    assert client.get("/v1/shares/" + share["token"]).status_code == 404

    uploaded = client.post(base + "/files", json={
        "filename": "notes.txt", "content": "hello", "content_type": "text/plain",
    }, headers=headers)
    assert uploaded.status_code == 201, uploaded.text
    file_id = uploaded.json()["id"]
    assert client.get(base + f"/files/{file_id}", headers=headers).text == "hello"
    assert client.delete(base + f"/files/{file_id}", headers=headers).status_code == 200

    dataset = client.post(base + "/evaluations/datasets", json={
        "name": "smoke", "cases": [{"input": "1+1", "expected": 2, "actual": 2},
                                     {"input": "2+2", "expected": 4, "actual": 3}],
    }, headers=headers)
    assert dataset.status_code == 201, dataset.text
    result = client.post(base + "/evaluations/runs", json={"dataset_id": dataset.json()["id"]}, headers=headers)
    assert result.status_code == 201, result.text
    assert result.json()["payload"]["summary"]["correct"] == 1


def test_legacy_cloud_model_cannot_be_selected_for_agent(tmp_path):
    client, headers, workspace = _client(tmp_path)
    base = f"/v1/workspaces/{workspace}"
    assert client.post(base + "/sources/sales", json={"relative_path": "sales.db"},
                       headers=headers).status_code == 201
    old_model = client.post(base + "/models", json={
        "name": "old cloud config", "config": {"provider": "deepseek", "model": "deepseek-chat"},
    }, headers=headers).json()
    health = client.get(base + f"/models/{old_model['id']}/health", headers=headers)
    assert health.json()["status"] == "unsupported_provider"
    ask = client.post(base + "/sources/sales/ask", json={
        "question": "有多少销售记录？", "model_id": old_model["id"],
    }, headers=headers)
    assert ask.status_code == 400
    assert "Only Ollama" in ask.json()["detail"]


def test_source_lifecycle_graph_and_sandbox_boundary(tmp_path):
    client, headers, workspace = _client(tmp_path)
    base = f"/v1/workspaces/{workspace}"
    registered = client.post(base + "/sources/sales", json={"relative_path": "sales.db"}, headers=headers)
    assert registered.status_code == 201
    assert client.post(base + "/sources/sales/test", headers=headers).json()["status"] == "ok"
    assert client.delete(base + "/sources/sales", headers=headers).json()["status"] == "deleted"

    kb = client.post(base + "/knowledge-bases", json={"name": "Rules"}, headers=headers).json()["id"]
    entity = client.post(base + f"/knowledge-bases/{kb}/graph/entities", json={
        "name": "refund", "entity_type": "policy", "properties": {"days": 7},
    }, headers=headers)
    assert entity.status_code == 201
    graph = client.get(base + f"/knowledge-bases/{kb}/graph", headers=headers).json()
    assert graph["entities"][0]["name"] == "refund"
    chunks = client.get(base + f"/knowledge-bases/{kb}/chunks", headers=headers)
    assert chunks.status_code == 200 and chunks.json()["chunks"] == []

    sandbox = client.post(base + "/sandboxes/run", json={"command": ["python", "-c", "print(1)"]}, headers=headers)
    assert sandbox.status_code == 403


def test_connector_skill_schedule_and_feedback_surfaces(tmp_path):
    client, headers, workspace = _client(tmp_path)
    base = f"/v1/workspaces/{workspace}"
    connector = client.post(base + "/connectors", json={
        "name": "local-mcp", "config": {"endpoint": ""},
    }, headers=headers)
    assert connector.status_code == 201
    assert client.post(base + f"/connectors/{connector.json()['id']}/test", headers=headers).json()["status"] == "configured"

    skill = client.post(base + "/skills", json={
        "name": "sql-reader", "config": {"instructions": "只读查询", "tools": ["query_database"]},
    }, headers=headers).json()
    content = client.get(base + f"/skills/{skill['id']}/content", headers=headers).json()
    assert content["tools"] == ["query_database"]

    schedule = client.post(base + "/schedules", json={
        "name": "daily", "config": {"payload": {"question": "日报"}}, "cron": "0 9 * * *",
    }, headers=headers).json()
    triggered = client.post(base + f"/schedules/{schedule['id']}/trigger", headers=headers)
    assert triggered.status_code == 201
    assert triggered.json()["payload"]["status"] == "queued"

    client.app.state.runs.create_run("feedback-run", workspace, "test")
    feedback = client.post(base + "/feedback", json={
        "run_id": "feedback-run", "rating": 5, "comment": "清晰",
    }, headers=headers)
    assert feedback.status_code == 201
    assert client.get(base + "/feedback?run_id=feedback-run", headers=headers).json()["feedback"][0]["rating"] == 5

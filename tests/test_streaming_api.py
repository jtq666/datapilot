import json
import sqlite3

from fastapi.testclient import TestClient

from data_platform.agents.contracts import ModelReply, ModelStreamChunk, ToolCall
from data_platform.api.app import create_app


def setup(tmp_path, model):
    data = tmp_path / "sources"
    data.mkdir()
    with sqlite3.connect(data / "customers.db") as db:
        db.executescript("CREATE TABLE customers(id INTEGER); INSERT INTO customers VALUES (1), (2);")
    app = create_app(data, tmp_path / "state", model_factory=lambda: model)
    app.state.identity.create_user("alice", "correct-long-password")
    client = TestClient(app)
    token = client.post("/v1/auth/login", json={
        "username": "alice", "password": "correct-long-password"
    }).json()["access_token"]
    headers = {"Authorization": f"Bearer {token}"}
    workspace = client.post("/v1/workspaces", json={"name": "Test"}, headers=headers).json()["id"]
    source = f"/v1/workspaces/{workspace}/sources/customers"
    assert client.post(source, json={"relative_path": "customers.db"}, headers=headers).status_code == 201
    return client, app, source, headers


def decode_sse(body):
    events = []
    for block in body.strip().split("\n\n"):
        lines = block.splitlines()
        kind = next(line[7:] for line in lines if line.startswith("event: "))
        data = json.loads(next(line[6:] for line in lines if line.startswith("data: ")))
        assert kind == data["type"]
        events.append(data)
    return events


def test_streaming_api_delivers_real_model_chunks_and_persists_answer(tmp_path):
    class StreamingModel:
        def stream_complete(self, messages, tools):
            if messages[-1]["role"] == "user":
                yield ModelStreamChunk(reply=ModelReply(tool_calls=(ToolCall(
                    "c1", "query_database", {"sql": "SELECT COUNT(*) FROM customers"}),)))
            else:
                assert '"rows": [[2]]' in messages[-1]["content"]
                yield ModelStreamChunk(text="共有 ")
                yield ModelStreamChunk(text="2 位客户")
                yield ModelStreamChunk(reply=ModelReply(content="共有 2 位客户"))

    client, app, source, headers = setup(tmp_path, StreamingModel())
    response = client.post(source + "/ask/stream", json={"question": "有多少客户？"}, headers=headers)
    assert response.status_code == 200, response.text
    assert response.headers["content-type"].startswith("text/event-stream")
    events = decode_sse(response.text)
    assert [item["type"] for item in events] == [
        "run_started", "model_finished", "tool_finished", "text_delta", "text_delta",
        "model_finished", "completed",
    ]
    assert [item["text"] for item in events if item["type"] == "text_delta"] == ["共有 ", "2 位客户"]
    cid = events[0]["conversation_id"]
    assert client.get(source.rsplit("/sources/", 1)[0] + f"/conversations/{cid}", headers=headers).json()[
        "turns"][0]["answer"] == "共有 2 位客户"
    assert app.state.runs.get_run(events[0]["run_id"], source.split("/")[3]).status == "succeeded"


def test_streaming_api_failure_does_not_store_partial_answer(tmp_path):
    class BrokenStream:
        def stream_complete(self, messages, tools):
            yield ModelStreamChunk(text="半截答案")
            raise RuntimeError("upstream broke")

    client, app, source, headers = setup(tmp_path, BrokenStream())
    response = client.post(source + "/ask/stream", json={"question": "问题"}, headers=headers)
    events = decode_sse(response.text)
    assert [item["type"] for item in events] == ["run_started", "text_delta", "error"]
    cid = events[0]["conversation_id"]
    detail = client.get(source.rsplit("/sources/", 1)[0] + f"/conversations/{cid}", headers=headers).json()
    assert detail["turns"][0]["status"] == "failed"
    assert detail["turns"][0]["answer"] is None
    assert app.state.runs.get_run(events[0]["run_id"], detail["workspace_id"]).status == "failed"


def test_streaming_endpoint_rejects_nonstream_model_without_fake_chunks(tmp_path):
    class SyncOnly:
        def complete(self, messages, tools):
            return ModelReply(content="not streamed")

    client, _, source, headers = setup(tmp_path, SyncOnly())
    response = client.post(source + "/ask/stream", json={"question": "问题"}, headers=headers)
    assert response.status_code == 503
    conversations_url = source.rsplit("/sources/", 1)[0] + "/conversations"
    assert client.get(conversations_url, headers=headers).json()["conversations"] == []

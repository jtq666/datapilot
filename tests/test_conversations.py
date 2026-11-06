import sqlite3

from fastapi.testclient import TestClient

from data_platform.agents.contracts import ModelReply
from data_platform.api.app import create_app


def setup_client(tmp_path, model):
    data = tmp_path / "data"
    data.mkdir()
    with sqlite3.connect(data / "sample.db") as db:
        db.execute("CREATE TABLE customers(id INTEGER)")
        db.execute("INSERT INTO customers VALUES (1)")
    app = create_app(data, tmp_path / "state", model_factory=lambda: model)
    alice_id = app.state.identity.create_user("alice", "alice-long-password")
    bob_id = app.state.identity.create_user("bob", "bob-long-password--")
    client = TestClient(app)
    alice_token = client.post("/v1/auth/login", json={"username": "alice", "password": "alice-long-password"}).json()["access_token"]
    bob_token = client.post("/v1/auth/login", json={"username": "bob", "password": "bob-long-password--"}).json()["access_token"]
    alice = {"Authorization": f"Bearer {alice_token}"}
    bob = {"Authorization": f"Bearer {bob_token}"}
    workspace = app.state.identity.create_workspace(alice_id, "Test")
    app.state.identity.add_member(alice_id, workspace, bob_id)
    source = f"/v1/workspaces/{workspace}/sources/sample"
    assert client.post(source, json={"relative_path": "sample.db"}, headers=alice).status_code == 201
    return client, app, source, alice, bob


def test_continue_list_isolate_and_delete_conversation(tmp_path):
    class MemoryModel:
        def complete(self, messages, tools):
            if messages[-1]["content"] == "第一个问题":
                return ModelReply(content="第一个回答")
            assert [(item["role"], item["content"]) for item in messages[-3:]] == [
                ("user", "第一个问题"), ("assistant", "第一个回答"), ("user", "接着问"),
            ]
            return ModelReply(content="第二个回答")

    client, app, source, alice, bob = setup_client(tmp_path, MemoryModel())
    first = client.post(source + "/ask", json={"question": "第一个问题"}, headers=alice)
    assert first.status_code == 200, first.text
    conversation_id = first.json()["conversation_id"]
    conversations_url = source.rsplit("/sources/", 1)[0] + "/conversations"
    assert client.get(conversations_url, headers=alice).json()["conversations"][0]["id"] == conversation_id
    assert client.get(conversations_url, headers=bob).json()["conversations"] == []
    assert client.get(f"{conversations_url}/{conversation_id}", headers=bob).status_code == 404
    assert client.post(source + "/ask", json={"question": "接着问", "conversation_id": conversation_id}, headers=bob).status_code == 404
    workspace = source.split("/")[3]
    run_url = f"/v1/workspaces/{workspace}/runs/{first.json()['run_id']}"
    assert client.get(run_url, headers=bob).status_code == 404
    assert client.get(run_url + "/events", headers=bob).status_code == 404
    assert client.get(f"/v1/workspaces/{workspace}/runs", headers=bob).json()["runs"] == []
    second = client.post(source + "/ask", json={"question": "接着问", "conversation_id": conversation_id}, headers=alice)
    assert second.status_code == 200, second.text
    assert second.json()["answer"] == "第二个回答"
    detail = client.get(f"{conversations_url}/{conversation_id}", headers=alice).json()
    assert [turn["status"] for turn in detail["turns"]] == ["succeeded", "succeeded"]
    assert [turn["answer"] for turn in detail["turns"]] == ["第一个回答", "第二个回答"]
    assert detail["source_name"] == "sample"
    assert client.delete(f"{conversations_url}/{conversation_id}", headers=bob).status_code == 404
    deleted = client.delete(f"{conversations_url}/{conversation_id}", headers=alice)
    assert deleted.json() == {"status": "deleted", "run_traces_retained": True}
    assert client.get(f"{conversations_url}/{conversation_id}", headers=alice).status_code == 404
    assert app.state.runs.get_run(first.json()["run_id"], detail["workspace_id"]) is not None
    assert client.get(run_url, headers=bob).status_code == 404


def test_failed_turn_is_not_replayed_as_successful_history(tmp_path):
    class FlakyModel:
        def __init__(self):
            self.calls = 0

        def complete(self, messages, tools):
            self.calls += 1
            if self.calls == 2:
                raise RuntimeError("temporary outage")
            if self.calls == 3:
                assert [message["content"] for message in messages if message["role"] == "user"] == [
                    "成功问题", "恢复后问题"
                ]
            return ModelReply(content=f"回答{self.calls}")

    client, _, source, alice, _ = setup_client(tmp_path, FlakyModel())
    first = client.post(source + "/ask", json={"question": "成功问题"}, headers=alice).json()
    cid = first["conversation_id"]
    assert client.post(source + "/ask", json={"question": "失败问题", "conversation_id": cid}, headers=alice).status_code == 502
    recovered = client.post(source + "/ask", json={"question": "恢复后问题", "conversation_id": cid}, headers=alice)
    assert recovered.status_code == 200, recovered.text
    url = source.rsplit("/sources/", 1)[0] + f"/conversations/{cid}"
    assert [turn["status"] for turn in client.get(url, headers=alice).json()["turns"]] == [
        "succeeded", "failed", "succeeded"
    ]

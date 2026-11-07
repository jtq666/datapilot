from fastapi.testclient import TestClient
from langgraph.checkpoint.memory import InMemorySaver

from datapilot.api import create_app


class Members:
    def member(self, workspace, name):
        return workspace == "demo" and name in {"analyst", "reviewer"}

    def issue_token(self, name):
        return "test-token-" + name


def test_demo_role_requires_explicit_opt_in_and_local_browser():
    origin = {"Origin": "http://127.0.0.1:5174"}
    role = {"role": "reviewer"}
    with TestClient(create_app(Members(), None, None, None, InMemorySaver()),
                    base_url="http://127.0.0.1:18091", client=("127.0.0.1", 5000)) as client:
        assert client.get("/auth/options", headers=origin).json() == {"local_demo": False}
        assert client.post("/auth/demo", headers=origin, json=role).status_code == 404

    app = create_app(Members(), None, None, None, InMemorySaver(), local_demo=True)
    with TestClient(app, base_url="http://127.0.0.1:18091", client=("127.0.0.1", 5000)) as client:
        assert client.get("/auth/options", headers=origin).json() == {"local_demo": True}
        assert client.post("/auth/demo", json=role).status_code == 404
        assert client.post("/auth/demo", headers={"Origin": "https://example.com"}, json=role).status_code == 404
        assert client.post("/auth/demo", headers=origin, json={"role": "other"}).status_code == 422
        assert client.post("/auth/demo", headers=origin, json=role).json()["token"] == "test-token-reviewer"

    with TestClient(app, base_url="http://127.0.0.1:18091", client=("192.0.2.1", 5000)) as client:
        assert client.post("/auth/demo", headers=origin, json=role).status_code == 404

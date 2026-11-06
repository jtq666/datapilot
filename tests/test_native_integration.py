"""Deterministic HTTP-protocol test; does not spend model tokens."""

import json

import httpx

from data_platform.agents.runner import AgentRunner
from data_platform.agents.tools import ToolDefinition, ToolRegistry
from data_platform.model_gateway.deepseek import OllamaChatProvider
from data_platform.runtime.models import RunStatus
from data_platform.runtime.store import RunStore


def test_chat_http_to_tool_and_back(tmp_path):
    requests = []

    def handle(request):
        body = json.loads(request.content)
        requests.append(body)
        if len(requests) == 1:
            return httpx.Response(200, json={
                "choices": [{"finish_reason": "tool_calls", "message": {
                    "content": None,
                    "tool_calls": [{"id": "sql-1", "type": "function", "function": {
                        "name": "count_students", "arguments": "{}",
                    }}],
                }}],
            })
        tool_message = body["messages"][-1]
        assert tool_message["role"] == "tool"
        assert tool_message["tool_call_id"] == "sql-1"
        assert json.loads(tool_message["content"])["data"] == 10
        return httpx.Response(200, json={
            "choices": [{"finish_reason": "stop", "message": {"content": "共有 10 人"}}],
        })

    store = RunStore(tmp_path / "runs.db")
    registry = ToolRegistry()
    registry.register(ToolDefinition(
        "count_students", "Count students", {"type": "object", "properties": {}, "additionalProperties": False},
        lambda _: 10,
    ))
    provider = OllamaChatProvider(client=httpx.Client(transport=httpx.MockTransport(handle)))
    run_id = AgentRunner(store, provider, registry).start("workspace-1", "学生有多少人？")
    assert store.get_run(run_id, "workspace-1").status == RunStatus.SUCCEEDED
    assert store.load_checkpoint(run_id, "workspace-1")[1]["answer"] == "共有 10 人"
    assert len(requests) == 2
    assert len(store.events(run_id, "workspace-1")) == 4

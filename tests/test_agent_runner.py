from __future__ import annotations

from data_platform.agents.contracts import ModelReply, ModelStreamChunk, ToolCall
from data_platform.agents.runner import AgentRunner
from data_platform.agents.tools import ToolDefinition, ToolRegistry
from data_platform.runtime.models import RunStatus
from data_platform.runtime.store import RunStore


TOOL_SCHEMA = {
    "type": "object",
    "properties": {"value": {"type": "integer"}},
    "required": ["value"],
    "additionalProperties": False,
}


class ScriptedModel:
    def __init__(self, replies):
        self.replies = iter(replies)
        self.messages_seen = []

    def complete(self, messages, tools):
        self.messages_seen.append(list(messages))
        return next(self.replies)


def test_native_tool_loop_and_trace(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    tools = ToolRegistry()
    tools.register(ToolDefinition("double", "Double an integer", TOOL_SCHEMA, lambda args: args["value"] * 2))
    model = ScriptedModel([
        ModelReply(tool_calls=(ToolCall("call-1", "double", {"value": 4}),)),
        ModelReply(content="The result is 8"),
    ])
    run_id = AgentRunner(store, model, tools).start("space-1", "Double 4")
    assert store.get_run(run_id, "space-1").status == RunStatus.SUCCEEDED
    assert store.load_checkpoint(run_id, "space-1")[1]["answer"] == "The result is 8"
    assert '"data": 8' in model.messages_seen[1][-1]["content"]
    assert [event.kind for event in store.events(run_id, "space-1")] == [
        "model-finished", "tool-finished", "model-finished", "status-changed"
    ]


def test_invalid_args_return_structured_error(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    tools = ToolRegistry()
    tools.register(ToolDefinition("double", "Double an integer", TOOL_SCHEMA, lambda args: args["value"] * 2))
    model = ScriptedModel([
        ModelReply(tool_calls=(ToolCall("call-1", "double", {"value": "bad"}),)),
        ModelReply(content="I could not calculate that"),
    ])
    AgentRunner(store, model, tools).start("space-1", "Double bad")
    assert '"error_code": "invalid_arguments"' in model.messages_seen[1][-1]["content"]
    assert model.messages_seen[1][-1]["role"] == "tool"  # Tool errors remain structured tool results.


def test_write_tool_cannot_run_without_approval(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    called = []
    tools = ToolRegistry()
    tools.register(ToolDefinition("write", "Write a value", TOOL_SCHEMA, lambda args: called.append(args), read_only=False))
    model = ScriptedModel([
        ModelReply(tool_calls=(ToolCall("call-1", "write", {"value": 4}),)),
        ModelReply(content="Approval is required"),
    ])
    AgentRunner(store, model, tools).start("space-1", "Write 4")
    assert called == []
    assert '"error_code": "permission_required"' in model.messages_seen[1][-1]["content"]


def test_model_failure_then_resume_from_checkpoint(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    tools = ToolRegistry()
    model = ScriptedModel([RuntimeError("temporary outage")])
    def failing_complete(*_):
        raise RuntimeError("temporary outage")
    model.complete = failing_complete
    runner = AgentRunner(store, model, tools)
    import pytest
    with pytest.raises(RuntimeError):
        runner.start("space-1", "Hello", run_id="run-1")
    assert store.get_run("run-1", "space-1").status == RunStatus.FAILED
    runner.model = ScriptedModel([ModelReply(content="Recovered")])
    runner.resume("run-1", "space-1")
    assert store.get_run("run-1", "space-1").status == RunStatus.SUCCEEDED
    assert store.load_checkpoint("run-1", "space-1")[1]["answer"] == "Recovered"


def test_native_stream_emits_chunks_around_real_tool_execution(tmp_path):
    store = RunStore(tmp_path / "runs.db")
    tools = ToolRegistry()
    executed = []
    tools.register(ToolDefinition("double", "Double an integer", TOOL_SCHEMA,
                                  lambda args: executed.append(args["value"]) or args["value"] * 2))

    class StreamingModel:
        def stream_complete(self, messages, schemas):
            if messages[-1]["role"] == "user":
                yield ModelStreamChunk(reply=ModelReply(tool_calls=(ToolCall("c1", "double", {"value": 4}),)))
            else:
                assert '"data": 8' in messages[-1]["content"]
                yield ModelStreamChunk(text="结果")
                yield ModelStreamChunk(text="是 8")
                yield ModelStreamChunk(reply=ModelReply(content="结果是 8"))

    stream = AgentRunner(store, StreamingModel(), tools).stream_start("space-1", "Double 4")
    first = next(stream)
    assert first["type"] == "run_started"
    events = list(stream)
    assert executed == [4]
    assert [event["type"] for event in events] == [
        "model_finished", "tool_finished", "text_delta", "text_delta", "model_finished", "completed"
    ]
    assert [event["text"] for event in events if event["type"] == "text_delta"] == ["结果", "是 8"]
    assert store.get_run(first["run_id"], "space-1").status == RunStatus.SUCCEEDED


def test_disconnected_stream_is_failed_not_completed(tmp_path):
    store = RunStore(tmp_path / "runs.db")

    class PartialModel:
        def stream_complete(self, messages, schemas):
            yield ModelStreamChunk(text="半截")
            yield ModelStreamChunk(reply=ModelReply(content="半截答案"))

    stream = AgentRunner(store, PartialModel(), ToolRegistry()).stream_start("space-1", "Hello")
    run_id = next(stream)["run_id"]
    assert next(stream) == {"type": "text_delta", "text": "半截"}
    stream.close()
    assert store.get_run(run_id, "space-1").status == RunStatus.FAILED

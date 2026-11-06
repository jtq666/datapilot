import pytest

from data_platform.agents.contracts import ModelReply
from data_platform.agents.tools import ToolDefinition, ToolRegistry
from data_platform.runtime.models import RunStatus
from data_platform.runtime.store import RunStore
from data_platform.workflows.runner import WorkflowRunner
from data_platform.workflows.spec import Edge, Node, WorkflowSpec


def test_branching_workflow_returns_the_correct_path(tmp_path):
    spec = WorkflowSpec(
        start="check",
        nodes=(
            Node("check", "condition", {"key": "amount", "operator": "gte", "value": 100}),
            Node("large", "set", {"output_key": "label", "value": "large"}),
            Node("small", "set", {"output_key": "label", "value": "small"}),
            Node("done", "end", {"result_key": "label"}),
        ),
        edges=(Edge("check", "large", "true"), Edge("check", "small", "false"),
               Edge("large", "done"), Edge("small", "done")),
    )
    runs = RunStore(tmp_path / "runs.db")
    runner = WorkflowRunner(runs, ToolRegistry())
    first = runner.start("team-a", spec, {"amount": 180})
    second = runner.start("team-a", spec, {"amount": 20})
    assert runs.load_checkpoint(first, "team-a")[1]["answer"] == "large"
    assert runs.load_checkpoint(second, "team-a")[1]["answer"] == "small"
    assert runs.get_run(first, "team-a").status == RunStatus.SUCCEEDED
    with pytest.raises(LookupError):
        runs.events(first, "team-b")


def test_approval_waits_and_resumes_after_process_reopen(tmp_path):
    spec = WorkflowSpec(
        start="approve",
        nodes=(Node("approve", "approval", {"output_key": "accepted"}),
               Node("yes", "end", {"result_key": "accepted"}),
               Node("no", "end", {"result_key": "accepted"})),
        edges=(Edge("approve", "yes", "true"), Edge("approve", "no", "false")),
    )
    path = tmp_path / "runs.db"
    runs = RunStore(path)
    run_id = WorkflowRunner(runs, ToolRegistry()).start("team-a", spec, {})
    assert runs.get_run(run_id, "team-a").status == RunStatus.WAITING
    reopened = RunStore(path)
    with pytest.raises(ValueError, match="approval decision"):
        WorkflowRunner(reopened, ToolRegistry()).resume(run_id, "team-a")
    WorkflowRunner(reopened, ToolRegistry()).resume(run_id, "team-a", approval=False)
    assert reopened.get_run(run_id, "team-a").status == RunStatus.SUCCEEDED
    assert reopened.load_checkpoint(run_id, "team-a")[1]["answer"] is False
    assert "approval-requested" in [event.kind for event in reopened.events(run_id, "team-a")]


def test_tool_retry_and_model_node(tmp_path):
    attempts = []
    tools = ToolRegistry()

    def query(args):
        attempts.append(1)
        if len(attempts) == 1:
            raise RuntimeError("temporary")
        return {"total": args["amount"] * 2}

    tools.register(ToolDefinition("query", "Read data", {
        "type": "object", "properties": {"amount": {"type": "integer"}},
        "required": ["amount"], "additionalProperties": False,
    }, query))

    class Model:
        def complete(self, messages, tools):
            assert "'total': 14" in messages[0]["content"]
            return ModelReply(content="Total is 14")

    spec = WorkflowSpec(
        start="tool",
        nodes=(Node("tool", "tool", {"name": "query", "arguments": {"amount": {"from": "amount"}},
                                      "output_key": "result", "retries": 1}),
               Node("summarize", "model", {"prompt": {"from": "result"}, "output_key": "answer"}),
               Node("end", "end", {"result_key": "answer"})),
        edges=(Edge("tool", "summarize"), Edge("summarize", "end")),
    )
    runs = RunStore(tmp_path / "runs.db")
    run_id = WorkflowRunner(runs, tools, Model()).start("team-a", spec, {"amount": 7})
    assert len(attempts) == 2
    assert runs.load_checkpoint(run_id, "team-a")[1]["answer"] == "Total is 14"
    assert len([event for event in runs.events(run_id, "team-a") if event.kind == "tool-attempt"]) == 2


def test_loop_is_bounded_and_bad_edges_rejected(tmp_path):
    spec = WorkflowSpec(
        start="check", nodes=(
            Node("check", "condition", {"key": "repeat", "value": True}),
            Node("again", "set", {"output_key": "x", "value": 1}), Node("end", "end")
        ),
        edges=(Edge("check", "again", "true"), Edge("check", "end", "false"), Edge("again", "check")),
        max_steps=3,
    )
    runs = RunStore(tmp_path / "runs.db")
    run_id = WorkflowRunner(runs, ToolRegistry()).start("team-a", spec, {"repeat": True})
    assert runs.get_run(run_id, "team-a").status == RunStatus.FAILED
    assert len([event for event in runs.events(run_id, "team-a") if event.kind == "node-completed"]) == 3
    with pytest.raises(ValueError, match="edge references"):
        WorkflowSpec(start="x", nodes=(Node("x", "set", {"output_key": "x", "value": 1}), Node("end", "end")),
                     edges=(Edge("x", "missing"),)).validate()
    with pytest.raises(ValueError, match="reachable"):
        WorkflowSpec(start="x", nodes=(Node("x", "set", {"output_key": "x", "value": 1}), Node("end", "end")),
                     edges=(Edge("x", "x"),)).validate()

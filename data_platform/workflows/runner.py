from __future__ import annotations

import json
from typing import Any
from uuid import uuid4

from data_platform.agents.contracts import ModelProvider
from data_platform.agents.tools import ToolRegistry
from data_platform.runtime.models import RunEvent, RunStatus, utcnow
from data_platform.runtime.store import RunStore

from .spec import WorkflowSpec


def _resolve(value: Any, context: dict[str, Any]) -> Any:
    if isinstance(value, dict) and set(value) == {"from"}:
        key = value["from"]
        if key not in context:
            raise ValueError(f"missing workflow input: {key}")
        return context[key]
    return value


class WorkflowRunner:
    """Sequential, checkpointed graph runner shared with the Agent run store.

    Only registered read-only tools can execute. A future sandbox/approval
    executor is required for mutating tools and parallel nodes.
    """

    def __init__(self, runs: RunStore, tools: ToolRegistry, model: ModelProvider | None = None):
        self.runs = runs
        self.tools = tools
        self.model = model

    def start(
        self, workspace_id: str, spec: WorkflowSpec, inputs: dict[str, Any], *,
        run_id: str | None = None, bindings: dict[str, Any] | None = None,
    ) -> str:
        spec.validate()
        json.dumps(inputs)
        run_id = run_id or str(uuid4())
        self.runs.create_run(run_id, workspace_id, "workflow")
        state = {
            "spec": spec.to_dict(), "node": spec.start, "context": dict(inputs),
            "steps": 0, "awaiting": False, "answer": None, "bindings": bindings or {},
        }
        version = self.runs.save_checkpoint(run_id, workspace_id, state, 0)
        self._drive(run_id, workspace_id, spec, state, version)
        return run_id

    def resume(self, run_id: str, workspace_id: str, *, approval: bool | None = None) -> None:
        run = self.runs.get_run(run_id, workspace_id)
        if run is None or run.kind != "workflow":
            raise LookupError("workflow run not found")
        if run.status not in (RunStatus.WAITING, RunStatus.FAILED):
            raise ValueError("workflow run is not resumable")
        checkpoint = self.runs.load_checkpoint(run_id, workspace_id)
        if checkpoint is None:
            raise ValueError("workflow checkpoint missing")
        state = checkpoint[1]
        spec = WorkflowSpec.from_dict(state["spec"])
        if state["awaiting"] and approval is None:
            raise ValueError("approval decision is required")
        if not state["awaiting"] and approval is not None:
            raise ValueError("run is not awaiting an approval decision")
        version = self.runs.transition(run_id, workspace_id, run.version, RunStatus.RUNNING).version
        if state["awaiting"]:
            node = next(node for node in spec.nodes if node.id == state["node"])
            output_key = node.config.get("output_key", "approved")
            state["context"][output_key] = approval
            state["node"] = spec.next_node(node.id, "true" if approval else "false")
            state["awaiting"] = False
            state["steps"] += 1
            self._event(run_id, workspace_id, node.id, "approval-decided", {"approved": approval})
            version = self.runs.save_checkpoint(run_id, workspace_id, state, version)
        self._drive(run_id, workspace_id, spec, state, version)

    def _event(self, run_id: str, workspace_id: str, step_id: str, kind: str, payload: dict[str, Any]) -> None:
        self.runs.append_event(RunEvent(str(uuid4()), run_id, step_id, kind, utcnow(), payload), workspace_id)

    def _drive(self, run_id: str, workspace_id: str, spec: WorkflowSpec, state: dict[str, Any], version: int) -> None:
        nodes = {node.id: node for node in spec.nodes}
        try:
            while state["steps"] < spec.max_steps:
                node = nodes[state["node"]]
                kind, config, context = node.kind, node.config, state["context"]
                if kind == "approval":
                    state["awaiting"] = True
                    version = self.runs.save_checkpoint(run_id, workspace_id, state, version)
                    self._event(run_id, workspace_id, node.id, "approval-requested", {})
                    self.runs.transition(run_id, workspace_id, version, RunStatus.WAITING)
                    return
                if kind == "end":
                    state["answer"] = context.get(config.get("result_key")) if config.get("result_key") else context
                    version = self.runs.save_checkpoint(run_id, workspace_id, state, version)
                    self._event(run_id, workspace_id, node.id, "workflow-completed", {"steps": state["steps"]})
                    self.runs.transition(run_id, workspace_id, version, RunStatus.SUCCEEDED)
                    return
                branch = None
                if kind == "set":
                    context[config["output_key"]] = _resolve(config["value"], context)
                elif kind == "condition":
                    left = context.get(config["key"])
                    operator = config.get("operator", "equals")
                    right = _resolve(config.get("value"), context)
                    if operator == "equals":
                        decision = left == right
                    elif operator == "exists":
                        decision = config["key"] in context
                    elif operator == "gte":
                        decision = left is not None and left >= right
                    else:
                        raise ValueError("unsupported condition operator")
                    branch = "true" if decision else "false"
                elif kind == "tool":
                    tool_name = config["name"]
                    if not self.tools.is_read_only(tool_name):
                        raise PermissionError("workflow tool is not registered as read-only")
                    arguments = {key: _resolve(value, context) for key, value in config.get("arguments", {}).items()}
                    retries = min(max(int(config.get("retries", 0)), 0), 3)
                    for attempt in range(retries + 1):
                        result = self.tools.execute(tool_name, arguments)
                        self._event(run_id, workspace_id, node.id, "tool-attempt", {
                            "tool": tool_name, "attempt": attempt + 1, "ok": result.ok,
                            "error_code": result.error_code,
                        })
                        if result.ok:
                            break
                    if not result.ok:
                        raise RuntimeError(f"workflow tool failed: {result.error_code}")
                    context[config["output_key"]] = result.data
                elif kind == "model":
                    if self.model is None:
                        raise ValueError("workflow model is not configured")
                    prompt = _resolve(config["prompt"], context)
                    reply = self.model.complete([{"role": "user", "content": str(prompt)}], [])
                    if reply.tool_calls:
                        raise ValueError("workflow model node cannot issue tools")
                    context[config["output_key"]] = reply.content
                state["node"] = spec.next_node(node.id, branch)
                state["steps"] += 1
                self._event(run_id, workspace_id, node.id, "node-completed", {"kind": kind, "step": state["steps"]})
                version = self.runs.save_checkpoint(run_id, workspace_id, state, version)
            self._event(run_id, workspace_id, state["node"], "limit-reached", {"max_steps": spec.max_steps})
            self.runs.transition(run_id, workspace_id, version, RunStatus.FAILED, reason="step limit")
        except Exception:
            current = self.runs.get_run(run_id, workspace_id)
            if current is not None and current.status == RunStatus.RUNNING:
                self.runs.transition(run_id, workspace_id, current.version, RunStatus.FAILED, reason="workflow error")
            raise


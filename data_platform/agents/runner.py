from __future__ import annotations

import json
from dataclasses import asdict
from typing import Any, Iterator
from uuid import uuid4

from data_platform.runtime.models import RunEvent, RunStatus, utcnow
from data_platform.runtime.store import RunStore

from .contracts import ModelProvider, ModelReply
from .tools import ToolRegistry


class AgentRunner:
    """Native tool-call loop backed by the shared run/checkpoint store.

    Only read-only tools are allowed until a permission/approval system exists.
    If interrupted after a tool side effect but before checkpointing, exactly-once
    execution cannot be guaranteed. Read-only tools make retry safe for now.
    """

    def __init__(self, store: RunStore, model: ModelProvider, tools: ToolRegistry,
                 max_turns: int = 8):
        if max_turns < 1:
            raise ValueError("max_turns must be positive")
        self.store = store
        self.model = model
        self.tools = tools
        self.max_turns = max_turns

    def start(
        self, workspace_id: str, question: str, *, run_id: str | None = None,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> str:
        run_id, state, version = self._initialize(workspace_id, question, run_id, system_prompt, history)
        for _ in self._drive(run_id, workspace_id, state, version, streaming=False):
            pass
        return run_id

    def stream_start(
        self, workspace_id: str, question: str, *, run_id: str | None = None,
        system_prompt: str | None = None,
        history: list[dict[str, str]] | None = None,
    ) -> Iterator[dict[str, Any]]:
        if not callable(getattr(self.model, "stream_complete", None)):
            raise ValueError("model does not support native streaming")
        run_id, state, version = self._initialize(workspace_id, question, run_id, system_prompt, history)
        try:
            yield {"type": "run_started", "run_id": run_id}
            yield from self._drive(run_id, workspace_id, state, version, streaming=True)
        finally:
            current = self.store.get_run(run_id, workspace_id)
            if current is not None and current.status == RunStatus.RUNNING:
                self.store.transition(run_id, workspace_id, current.version, RunStatus.FAILED,
                                      reason="stream disconnected")

    def _initialize(
        self, workspace_id: str, question: str, run_id: str | None,
        system_prompt: str | None, history: list[dict[str, str]] | None,
    ) -> tuple[str, dict[str, Any], int]:
        if not question.strip():
            raise ValueError("question cannot be empty")
        run_id = run_id or str(uuid4())
        messages = ([{"role": "system", "content": system_prompt}] if system_prompt else [])
        if history:
            if len(history) % 2 or any(
                message.get("role") != ("user" if index % 2 == 0 else "assistant")
                or not isinstance(message.get("content"), str)
                for index, message in enumerate(history)
            ):
                raise ValueError("history must contain completed user/assistant pairs")
            messages.extend({"role": item["role"], "content": item["content"]} for item in history)
        messages.append({"role": "user", "content": question})
        self.store.create_run(run_id, workspace_id, "agent")
        state: dict[str, Any] = {
            "messages": messages,
            "turns": 0,
            "pending": [],
            "next_tool": 0,
            "answer": None,
        }
        version = self.store.save_checkpoint(run_id, workspace_id, state, 0)
        return run_id, state, version

    def resume(self, run_id: str, workspace_id: str) -> None:
        run = self.store.get_run(run_id, workspace_id)
        if run is None:
            raise LookupError("run not found in workspace")
        if run.status not in (RunStatus.FAILED, RunStatus.WAITING):
            raise ValueError("run is not resumable")
        checkpoint = self.store.load_checkpoint(run_id, workspace_id)
        if checkpoint is None:
            raise ValueError("run has no checkpoint")
        version = self.store.transition(run_id, workspace_id, run.version, RunStatus.RUNNING).version
        for _ in self._drive(run_id, workspace_id, checkpoint[1], version, streaming=False):
            pass

    def _event(self, run_id: str, workspace_id: str, kind: str, payload: dict[str, Any]) -> None:
        self.store.append_event(
            RunEvent(str(uuid4()), run_id, "agent", kind, utcnow(), payload), workspace_id
        )

    def _checkpoint(self, run_id: str, workspace_id: str, state: dict[str, Any], version: int) -> int:
        return self.store.save_checkpoint(run_id, workspace_id, state, version)

    def _execute_pending_tool(self, state: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
        """Execute one model-requested tool and append its observation to history."""
        pending = state["pending"]
        call = pending[state["next_tool"]]
        if call.get("invalid_arguments"):
            result = {"ok": False, "data": None, "error_code": "invalid_arguments",
                      "message": "Tool arguments were not valid JSON"}
        elif not self.tools.contains(call["name"]):
            result = asdict(self.tools.execute(call["name"], call["arguments"]))
        elif not self.tools.is_read_only(call["name"]):
            result = {"ok": False, "error_code": "permission_required", "message": "Tool needs approval"}
        else:
            result = asdict(self.tools.execute(call["name"], call["arguments"]))

        content = json.dumps(result, ensure_ascii=False, default=str)
        state["messages"].append({"role": "tool", "tool_call_id": call["id"], "content": content})
        state["next_tool"] += 1
        if state["next_tool"] == len(pending):
            state["pending"] = []
            state["next_tool"] = 0
        return call, result

    def _request_model(self, messages: list[dict[str, Any]], *, streaming: bool
                       ) -> Iterator[dict[str, Any]]:
        """Yield only live text events; return one complete model reply."""
        schemas = self.tools.schemas()
        if not streaming:
            return self.model.complete(messages, schemas)
        reply = None
        for chunk in self.model.stream_complete(messages, schemas):
            if chunk.reply is not None:
                if reply is not None:
                    raise ValueError("model emitted multiple terminal stream replies")
                reply = chunk.reply
            elif chunk.text:
                yield {"type": "text_delta", "text": chunk.text}
        if reply is None:
            raise ValueError("model stream has no terminal reply")
        return reply

    def _append_model_reply(self, state: dict[str, Any], reply: ModelReply) -> bool:
        """Save the assistant turn; return True when tools must run next."""
        assistant_message: dict[str, Any] = {"role": "assistant", "content": reply.content}
        if reply.tool_calls:
            assistant_message["tool_calls"] = [
                {"id": call.id, "type": "function", "function": {
                    "name": call.name, "arguments": json.dumps(call.arguments, ensure_ascii=False),
                }}
                for call in reply.tool_calls
            ]
            state["messages"].append(assistant_message)
            state["pending"] = [asdict(call) for call in reply.tool_calls]
            state["next_tool"] = 0
            return True
        state["messages"].append(assistant_message)
        state["answer"] = reply.content
        return False

    def _drive(self, run_id: str, workspace_id: str, state: dict[str, Any], version: int,
               *, streaming: bool) -> Iterator[dict[str, Any]]:
        try:
            while True:
                if state["pending"]:
                    call, result = self._execute_pending_tool(state)
                    # The audit event intentionally excludes arguments and raw tool output.
                    self._event(run_id, workspace_id, "tool-finished", {
                        "tool": call["name"], "call_id": call["id"], "ok": result["ok"],
                        "error_code": result["error_code"],
                    })
                    if streaming:
                        yield {"type": "tool_finished", "tool": call["name"], "ok": result["ok"],
                               "error_code": result["error_code"]}
                    version = self._checkpoint(run_id, workspace_id, state, version)
                    continue

                if state["turns"] >= self.max_turns:
                    self._event(run_id, workspace_id, "limit-reached", {"max_turns": self.max_turns})
                    self.store.transition(run_id, workspace_id, version, RunStatus.FAILED, reason="turn limit")
                    if streaming:
                        yield {"type": "failed", "reason": "turn limit"}
                    return

                reply: ModelReply = yield from self._request_model(state["messages"], streaming=streaming)
                state["turns"] += 1
                self._event(run_id, workspace_id, "model-finished", {
                    "turn": state["turns"], "tool_count": len(reply.tool_calls), "usage": reply.usage,
                })
                if streaming:
                    yield {"type": "model_finished", "turn": state["turns"],
                           "tool_count": len(reply.tool_calls)}
                needs_tools = self._append_model_reply(state, reply)
                version = self._checkpoint(run_id, workspace_id, state, version)
                if needs_tools:
                    continue
                self.store.transition(run_id, workspace_id, version, RunStatus.SUCCEEDED)
                if streaming:
                    yield {"type": "completed", "run_id": run_id, "answer": reply.content}
                return
        except BaseException:
            current = self.store.get_run(run_id, workspace_id)
            if current is not None and current.status == RunStatus.RUNNING:
                self.store.transition(run_id, workspace_id, current.version, RunStatus.FAILED, reason="execution error")
            raise


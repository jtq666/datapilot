from __future__ import annotations

import json
import os
from typing import Any, Iterator

import httpx

from data_platform.agents.contracts import ModelReply, ModelStreamChunk, ToolCall


class ModelProtocolError(RuntimeError):
    """A model response could not be safely consumed."""


class OpenAICompatibleChatProvider:
    """Chat Completions adapter shared by cloud and local model servers."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str,
        base_url: str,
        client: httpx.Client | None = None,
        timeout: float = 60.0,
        require_api_key: bool = True,
    ):
        self.api_key = api_key
        if require_api_key and not self.api_key:
            raise ValueError("model API key is required")
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.client = client or httpx.Client(timeout=timeout)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> ModelReply:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": False}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        response = self.client.post(
            f"{self.base_url}/chat/completions",
            headers=self._headers(),
            json=payload,
        )
        response.raise_for_status()
        try:
            body = response.json()
            choice = body["choices"][0]
            finish_reason = choice["finish_reason"]
            if finish_reason not in ("stop", "tool_calls"):
                raise ModelProtocolError(f"model did not finish normally: {finish_reason}")
            message = choice["message"]
            calls = []
            for raw_call in message.get("tool_calls") or []:
                raw_arguments = raw_call["function"]["arguments"]
                try:
                    arguments = json.loads(raw_arguments)
                    invalid = not isinstance(arguments, dict)
                except (ValueError, TypeError):
                    arguments = {}
                    invalid = True
                calls.append(ToolCall(
                    raw_call["id"], raw_call["function"]["name"], arguments, invalid,
                ))
            usage = body.get("usage") or {}
            usage = {
                key: value for key, value in usage.items()
                if key in ("prompt_tokens", "completion_tokens", "total_tokens")
                and isinstance(value, int)
            }
            return ModelReply(message.get("content") or "", tuple(calls), usage)
        except (KeyError, IndexError, TypeError) as exc:
            raise ModelProtocolError("Malformed model response") from exc

    def stream_complete(self, messages: list[dict[str, Any]], tools: list[dict[str, Any]]) -> Iterator[ModelStreamChunk]:
        payload: dict[str, Any] = {"model": self.model, "messages": messages, "stream": True,
                                   "stream_options": {"include_usage": True}}
        if tools:
            payload["tools"] = tools
            payload["tool_choice"] = "auto"
        content: list[str] = []
        fragments: dict[int, dict[str, str]] = {}
        usage: dict[str, int] = {}
        finish_reason = None
        done = False
        total_chars = 0
        with self.client.stream(
            "POST", f"{self.base_url}/chat/completions",
            headers=self._headers(), json=payload,
        ) as response:
            response.raise_for_status()
            for line in response.iter_lines():
                if not line.startswith("data:"):
                    continue
                raw = line[5:].strip()
                if raw == "[DONE]":
                    done = True
                    break
                if not raw:
                    continue
                try:
                    chunk = json.loads(raw)
                    choice = chunk["choices"][0]
                    delta = choice["delta"]
                    if choice.get("finish_reason") is not None:
                        finish_reason = choice["finish_reason"]
                    if isinstance(chunk.get("usage"), dict):
                        usage = {key: value for key, value in chunk["usage"].items()
                                 if key in ("prompt_tokens", "completion_tokens", "total_tokens")
                                 and isinstance(value, int)}
                    piece = delta.get("content") or ""
                    if not isinstance(piece, str):
                        raise TypeError("content delta must be a string")
                    if piece:
                        content.append(piece)
                        total_chars += len(piece)
                        yield ModelStreamChunk(text=piece)
                    for call in delta.get("tool_calls") or []:
                        index = call["index"]
                        if not isinstance(index, int) or index < 0:
                            raise TypeError("invalid tool index")
                        item = fragments.setdefault(index, {"id": "", "name": "", "arguments": ""})
                        if call.get("id"):
                            item["id"] = call["id"]
                        function = call.get("function") or {}
                        if function.get("name"):
                            item["name"] += function["name"]
                        part = function.get("arguments") or ""
                        if not isinstance(part, str):
                            raise TypeError("tool arguments delta must be a string")
                        item["arguments"] += part
                        total_chars += len(part)
                    if total_chars > 4_000_000:
                        raise ModelProtocolError("Stream exceeded size limit")
                except (ValueError, KeyError, IndexError, TypeError) as exc:
                    raise ModelProtocolError("Malformed streaming model response") from exc
        if not done or finish_reason not in ("stop", "tool_calls"):
            raise ModelProtocolError("Streaming model response ended without a valid finish")
        calls = []
        for index in sorted(fragments):
            item = fragments[index]
            if not item["id"] or not item["name"]:
                raise ModelProtocolError("Incomplete streamed tool call")
            try:
                arguments = json.loads(item["arguments"])
                invalid = not isinstance(arguments, dict)
            except (ValueError, TypeError):
                arguments = {}
                invalid = True
            calls.append(ToolCall(item["id"], item["name"], arguments, invalid))
        if (finish_reason == "tool_calls") != bool(calls):
            raise ModelProtocolError("Stream finish reason and tool calls disagree")
        yield ModelStreamChunk(reply=ModelReply("".join(content), tuple(calls), usage))

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self.api_key}"} if self.api_key else {}


class DeepSeekChatProvider(OpenAICompatibleChatProvider):
    """DeepSeek Chat Completions adapter kept for cloud-model compatibility."""

    def __init__(
        self,
        *,
        api_key: str | None = None,
        model: str = "deepseek-chat",
        base_url: str = "https://api.deepseek.com",
        client: httpx.Client | None = None,
        timeout: float = 60.0,
    ):
        super().__init__(
            api_key=api_key or os.getenv("DEEPSEEK_API_KEY"),
            model=model,
            base_url=base_url,
            client=client,
            timeout=timeout,
            require_api_key=True,
        )


class OllamaChatProvider(OpenAICompatibleChatProvider):
    """Local Ollama provider using its OpenAI-compatible Chat Completions API."""

    def __init__(
        self,
        *,
        model: str | None = None,
        base_url: str | None = None,
        api_key: str | None = "ollama",
        client: httpx.Client | None = None,
        timeout: float = 120.0,
    ):
        super().__init__(
            api_key=api_key,
            model=model or os.getenv("OLLAMA_MODEL", "qwen2.5:14b"),
            base_url=base_url or os.getenv("OLLAMA_BASE_URL", "http://127.0.0.1:11434/v1"),
            client=client,
            timeout=timeout,
            require_api_key=False,
        )


def create_default_chat_provider() -> OpenAICompatibleChatProvider:
    """Use local Ollama for normal Agent runs; keep the model interface testable."""
    provider = os.getenv("MODEL_PROVIDER", "ollama").strip().lower()
    if provider == "ollama":
        return OllamaChatProvider()
    raise ValueError("MODEL_PROVIDER must be ollama")


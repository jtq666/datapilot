import os
from time import sleep

import httpx


class DeepSeek:
    """Only the native, non-thinking tool-call protocol is exposed to the graph."""

    def __init__(self, client: httpx.Client):
        self.client = client
        self.key = os.getenv("DEEPSEEK_API_KEY")
        self.model = os.getenv("DEEPSEEK_MODEL", "deepseek-flash")

    def complete(self, messages, tools):
        if not self.key:
            raise RuntimeError("model_not_configured")
        payload = {"model": self.model, "messages": messages, "thinking": {"type": "disabled"},
                   "temperature": 0, "max_tokens": 1600}
        if tools:
            payload.update(tools=tools, tool_choice="auto")
        headers = {"Authorization": f"Bearer {self.key}"}
        response = self.client.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload)
        if response.status_code in {429, 500, 502, 503, 504}:
            sleep(2)
            response = self.client.post("https://api.deepseek.com/chat/completions", headers=headers, json=payload)
        response.raise_for_status()
        choice = response.json()["choices"][0]
        raw = choice["message"]
        calls = raw.get("tool_calls") or []
        if choice["finish_reason"] != ("tool_calls" if calls else "stop"):
            raise ValueError("incomplete model response")
        return {"role": "assistant", "content": raw.get("content"), **({"tool_calls": calls} if calls else {})}

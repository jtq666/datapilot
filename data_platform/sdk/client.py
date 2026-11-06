from __future__ import annotations

from typing import Any

import httpx


class DataAgentClient:
    """Small synchronous SDK for the local HTTP contract."""

    def __init__(self, base_url: str = "http://127.0.0.1:18090", token: str | None = None,
                 *, timeout: float = 10.0):
        self.base_url = base_url.rstrip("/")
        self.token = token
        self.timeout = timeout

    def _request(self, method: str, path: str, **kwargs: Any) -> Any:
        headers = dict(kwargs.pop("headers", {}) or {})
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        response = httpx.request(method, self.base_url + "/v1" + path,
                                  headers=headers, timeout=self.timeout, **kwargs)
        response.raise_for_status()
        return response.json()

    def health(self) -> dict:
        return self._request("GET", "/health")

    def login(self, username: str, password: str) -> str:
        result = self._request("POST", "/auth/login", json={"username": username, "password": password})
        self.token = result["access_token"]
        return self.token

    def workspaces(self) -> list[dict]:
        return self._request("GET", "/workspaces")["workspaces"]

    def ask(self, workspace_id: str, source_name: str, question: str,
            knowledge_base_id: str | None = None) -> dict:
        return self._request("POST", f"/workspaces/{workspace_id}/sources/{source_name}/ask",
                             json={"question": question, "knowledge_base_id": knowledge_base_id})

from __future__ import annotations

import math
import os

import httpx


class JinaEmbeddingProvider:
    """Task-specific v3 embeddings for documents and queries."""

    def __init__(
        self, *, api_key: str | None = None, model: str = "jina-embeddings-v3",
        dimensions: int = 1024, client: httpx.Client | None = None,
    ):
        self.api_key = api_key or os.getenv("JINA_API_KEY")
        if not self.api_key:
            raise ValueError("JINA_API_KEY is required")
        if not 32 <= dimensions <= 1024:
            raise ValueError("dimensions must be between 32 and 1024")
        self.model = model
        self.dimensions = dimensions
        self.client = client or httpx.Client(timeout=60)
        self._owns_client = client is None

    def close(self) -> None:
        if self._owns_client:
            self.client.close()

    def passages(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "retrieval.passage")

    def queries(self, texts: list[str]) -> list[list[float]]:
        return self._embed(texts, "retrieval.query")

    def _embed(self, texts: list[str], task: str) -> list[list[float]]:
        if not texts:
            return []
        response = self.client.post(
            "https://api.jina.ai/v1/embeddings",
            headers={"Authorization": f"Bearer {self.api_key}"},
            json={"model": self.model, "task": task, "dimensions": self.dimensions, "input": texts},
        )
        response.raise_for_status()
        try:
            data = sorted(response.json()["data"], key=lambda item: item["index"])
            vectors = [item["embedding"] for item in data]
            if len(vectors) != len(texts) or any(
                len(vector) != self.dimensions or any(not math.isfinite(float(value)) for value in vector)
                for vector in vectors
            ):
                raise ValueError("unexpected embedding dimensions or values")
            return [[float(value) for value in vector] for vector in vectors]
        except (KeyError, TypeError, ValueError) as exc:
            raise ValueError("invalid embedding response") from exc


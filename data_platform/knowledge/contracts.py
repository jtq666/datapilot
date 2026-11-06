from __future__ import annotations

from dataclasses import dataclass
from typing import Protocol


class EmbeddingProvider(Protocol):
    def passages(self, texts: list[str]) -> list[list[float]]: ...

    def queries(self, texts: list[str]) -> list[list[float]]: ...


@dataclass(frozen=True)
class SearchHit:
    chunk_id: str
    document_id: str
    filename: str
    ordinal: int
    text: str
    score: float
    keyword_rank: int | None
    vector_rank: int | None


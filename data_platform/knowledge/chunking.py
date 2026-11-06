from __future__ import annotations

import re


def split_text(text: str, *, size: int = 1000, overlap: int = 150) -> list[str]:
    if size < 100 or not 0 <= overlap < size // 2:
        raise ValueError("invalid chunk size or overlap")
    text = text.replace("\r\n", "\n").strip()
    if not text:
        return []
    chunks: list[str] = []
    start = 0
    while start < len(text):
        end = min(start + size, len(text))
        if end < len(text):
            candidates = [text.rfind(marker, start + size // 2, end) for marker in ("\n", "。", ".", "！", "？")]
            boundary = max(candidates)
            if boundary > start:
                end = boundary + 1
        chunks.append(text[start:end].strip())
        if end == len(text):
            break
        start = max(start + 1, end - overlap)
    return [chunk for chunk in chunks if chunk]


def search_tokens(text: str) -> list[str]:
    """Index English words plus Chinese characters and adjacent bigrams."""
    units = re.findall(r"[a-z0-9_]+|[\u4e00-\u9fff]", text.lower())
    tokens = list(units)
    tokens.extend(
        units[i] + units[i + 1] for i in range(len(units) - 1)
        if len(units[i]) == len(units[i + 1]) == 1
        and "\u4e00" <= units[i] <= "\u9fff"
        and "\u4e00" <= units[i + 1] <= "\u9fff"
    )
    return tokens


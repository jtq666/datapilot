"""Disposable browser QA server with a deterministic model, never production configuration."""

from __future__ import annotations

import sqlite3
import time
from pathlib import Path

import uvicorn

from data_platform.agents.contracts import ModelReply, ModelStreamChunk, ToolCall
from data_platform.api.app import create_app


class DeterministicModel:
    def complete(self, messages, tools):
        if messages[-1]["role"] == "tool":
            continuing = sum(message["role"] == "user" for message in messages) > 1
            return ModelReply(content="继续分析：共有 2 位客户。" if continuing else "共有 2 位客户。")
        return ModelReply(tool_calls=(ToolCall("qa-count", "query_database", {
            "sql": "SELECT COUNT(*) AS count FROM customers"
        }),))

    def stream_complete(self, messages, tools):
        if messages[-1]["role"] == "user":
            yield ModelStreamChunk(reply=ModelReply(tool_calls=(ToolCall("qa-count", "query_database", {
                "sql": "SELECT COUNT(*) AS count FROM customers"
            }),)))
            return
        continuing = sum(message["role"] == "user" for message in messages) > 1
        pieces = ("继续分析：", "共有 2 位客户。") if continuing else ("共有 ", "2 位客户。")
        for piece in pieces:
            time.sleep(0.6)
            yield ModelStreamChunk(text=piece)
        yield ModelStreamChunk(reply=ModelReply(content="".join(pieces)))


def main():
    root = Path("F:/shixi/_resources/conversation-browser-qa")
    source_root = root / "sources"
    source_root.mkdir(parents=True, exist_ok=True)
    sample = source_root / "sample.db"
    with sqlite3.connect(sample) as db:
        db.executescript("CREATE TABLE IF NOT EXISTS customers(id INTEGER PRIMARY KEY);")
        if db.execute("SELECT COUNT(*) FROM customers").fetchone()[0] == 0:
            db.executemany("INSERT INTO customers VALUES (?)", [(1,), (2,)])
    app = create_app(source_root, root / "state", model_factory=DeterministicModel)
    try:
        app.state.identity.create_user("qa_conversation", "Local-Conversation-QA-Only")
    except sqlite3.IntegrityError:
        pass
    uvicorn.run(app, host="127.0.0.1", port=18090)


if __name__ == "__main__":
    main()

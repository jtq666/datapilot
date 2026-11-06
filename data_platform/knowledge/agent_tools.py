from __future__ import annotations

from dataclasses import asdict

from data_platform.agents.tools import ToolDefinition, ToolRegistry

from .contracts import EmbeddingProvider
from .store import KnowledgeStore


def create_knowledge_tools(
    store: KnowledgeStore, workspace_id: str, kb_id: str, embeddings: EmbeddingProvider,
) -> ToolRegistry:
    tools = ToolRegistry()
    tools.register(ToolDefinition(
        "search_knowledge",
        "Search the selected knowledge base; returns text, filename, chunk number and score for citation",
        {
            "type": "object",
            "properties": {"query": {"type": "string"}},
            "required": ["query"],
            "additionalProperties": False,
        },
        lambda args: [asdict(hit) for hit in store.search(workspace_id, kb_id, args["query"], embeddings)],
    ))
    return tools

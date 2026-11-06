from __future__ import annotations

from dataclasses import asdict, dataclass, field
import json
from typing import Any


NODE_KINDS = {"set", "condition", "tool", "approval", "model", "end"}


@dataclass(frozen=True)
class Node:
    id: str
    kind: str
    config: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class Edge:
    source: str
    target: str
    label: str | None = None


@dataclass(frozen=True)
class WorkflowSpec:
    start: str
    nodes: tuple[Node, ...]
    edges: tuple[Edge, ...]
    max_steps: int = 30

    def validate(self) -> None:
        if not 1 <= self.max_steps <= 200 or not self.nodes or len(self.nodes) > 100 or len(self.edges) > 300:
            raise ValueError("workflow needs nodes and a step limit of 1-200")
        try:
            encoded = json.dumps(self.to_dict())
        except (TypeError, ValueError) as exc:
            raise ValueError("workflow configuration must be JSON serializable") from exc
        if len(encoded) > 200_000:
            raise ValueError("workflow specification is too large")
        by_id = {node.id: node for node in self.nodes}
        if len(by_id) != len(self.nodes) or self.start not in by_id:
            raise ValueError("node ids must be unique and start must exist")
        if not any(node.kind == "end" for node in self.nodes):
            raise ValueError("workflow needs an end node")
        for node in self.nodes:
            if not node.id or node.kind not in NODE_KINDS:
                raise ValueError("invalid node id or kind")
            if not isinstance(node.config, dict):
                raise ValueError("node config must be an object")
            if node.kind == "set" and (not isinstance(node.config.get("output_key"), str) or "value" not in node.config):
                raise ValueError("set node needs output_key and value")
            if node.kind == "condition" and (
                not isinstance(node.config.get("key"), str)
                or node.config.get("operator", "equals") not in {"equals", "exists", "gte"}
                or (node.config.get("operator", "equals") != "exists" and "value" not in node.config)
            ):
                raise ValueError("condition node has invalid configuration")
            if node.kind == "tool" and (
                not isinstance(node.config.get("name"), str)
                or not isinstance(node.config.get("output_key"), str)
                or not isinstance(node.config.get("arguments", {}), dict)
                or not isinstance(node.config.get("retries", 0), int)
                or not 0 <= node.config.get("retries", 0) <= 3
            ):
                raise ValueError("tool node has invalid configuration")
            if node.kind == "model" and (
                "prompt" not in node.config or not isinstance(node.config.get("output_key"), str)
            ):
                raise ValueError("model node needs prompt and output_key")
            if node.kind == "approval" and not isinstance(node.config.get("output_key", "approved"), str):
                raise ValueError("approval output_key must be a string")
            if node.kind == "end" and node.config.get("result_key") is not None and not isinstance(node.config["result_key"], str):
                raise ValueError("end result_key must be a string")
            outgoing = [edge for edge in self.edges if edge.source == node.id]
            labels = [edge.label for edge in outgoing]
            if node.kind == "end" and outgoing:
                raise ValueError("end node cannot have outgoing edges")
            if node.kind in ("condition", "approval") and set(labels) != {"true", "false"}:
                raise ValueError("condition and approval nodes need true/false edges")
            if node.kind in ("set", "tool", "model") and len(outgoing) != 1:
                raise ValueError("non-branching node needs exactly one outgoing edge")
            if node.kind == "tool" and labels != [None]:
                raise ValueError("tool node currently uses one default edge")
            if node.kind in ("set", "model") and labels != [None]:
                raise ValueError("non-branching edge must not have a label")
        if any(edge.source not in by_id or edge.target not in by_id for edge in self.edges):
            raise ValueError("edge references an unknown node")
        if len({(edge.source, edge.label) for edge in self.edges}) != len(self.edges):
            raise ValueError("branch labels must be unique per node")
        reachable = {self.start}
        frontier = [self.start]
        while frontier:
            source = frontier.pop()
            for edge in self.edges:
                if edge.source == source and edge.target not in reachable:
                    reachable.add(edge.target)
                    frontier.append(edge.target)
        if not any(by_id[node_id].kind == "end" for node_id in reachable):
            raise ValueError("no end node is reachable from start")

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "WorkflowSpec":
        try:
            spec = cls(
                start=data["start"],
                nodes=tuple(Node(**node) for node in data["nodes"]),
                edges=tuple(Edge(**edge) for edge in data["edges"]),
                max_steps=data.get("max_steps", 30),
            )
            spec.validate()
            return spec
        except (KeyError, TypeError) as exc:
            raise ValueError("invalid workflow specification") from exc

    def next_node(self, current: str, label: str | None = None) -> str:
        return next(edge.target for edge in self.edges if edge.source == current and edge.label == label)


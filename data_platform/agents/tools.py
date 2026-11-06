from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable

from jsonschema import Draft202012Validator


class ToolExecutionError(RuntimeError):
    def __init__(self, code: str, message: str):
        self.code = code
        self.safe_message = message
        super().__init__(message)


@dataclass(frozen=True)
class ToolResult:
    ok: bool
    data: Any = None
    error_code: str | None = None
    message: str | None = None


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    parameters: dict[str, Any]
    handler: Callable[[dict[str, Any]], Any]
    read_only: bool = True


class ToolRegistry:
    def __init__(self) -> None:
        self._tools: dict[str, ToolDefinition] = {}

    def register(self, tool: ToolDefinition) -> None:
        if not tool.name or tool.name in self._tools:
            raise ValueError("tool name must be unique and nonempty")
        Draft202012Validator.check_schema(tool.parameters)
        self._tools[tool.name] = tool

    def schemas(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": tool.name,
                    "description": tool.description,
                    "parameters": tool.parameters,
                },
            }
            for tool in self._tools.values()
        ]

    def contains(self, name: str) -> bool:
        return name in self._tools

    def add_from(self, other: "ToolRegistry") -> None:
        for tool in other._tools.values():
            self.register(tool)

    def restrict(self, allowed: set[str]) -> None:
        """Keep only explicitly allowed tools for a configured Agent."""
        self._tools = {name: tool for name, tool in self._tools.items() if name in allowed}

    def names(self) -> tuple[str, ...]:
        return tuple(self._tools)

    def execute(self, name: str, arguments: dict[str, Any]) -> ToolResult:
        tool = self._tools.get(name)
        if tool is None:
            return ToolResult(False, error_code="unknown_tool", message="Tool not registered")
        errors = list(Draft202012Validator(tool.parameters).iter_errors(arguments))
        if errors:
            return ToolResult(False, error_code="invalid_arguments", message=errors[0].message)
        try:
            return ToolResult(True, data=tool.handler(arguments))
        except ToolExecutionError as exc:
            return ToolResult(False, error_code=exc.code, message=exc.safe_message)
        except Exception:
            # Raw exception text could contain a secret or database credential.
            return ToolResult(False, error_code="tool_failed", message="Tool execution failed")

    def is_read_only(self, name: str) -> bool:
        tool = self._tools.get(name)
        return tool is not None and tool.read_only


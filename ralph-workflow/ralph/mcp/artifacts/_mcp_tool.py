"""MCPTool — represents an MCP tool with a handler."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:

    class _ToolHandler(Protocol):
        """Protocol for MCP tool handler callables."""

        def __call__(self, *args: object, **kwargs: object) -> dict[str, object]: ...


@dataclass
class MCPTool:
    """Represents an MCP tool.

    Attributes:
        name: Tool name.
        description: Tool description.
        input_schema: JSON schema for tool input.
        handler: Callable that handles tool invocations.
    """

    name: str
    description: str
    input_schema: dict[str, object]
    handler: _ToolHandler


__all__ = ["MCPTool"]

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._tool_definition import ToolDefinition


@dataclass(frozen=True)
class ToolMetadata:
    definition: ToolDefinition
    required_capability: str
    is_mutating: bool | None = None

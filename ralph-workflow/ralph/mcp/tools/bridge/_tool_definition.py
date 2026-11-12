from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._types import JsonObject


@dataclass(frozen=True)
class ToolDefinition:
    name: str
    description: str
    input_schema: JsonObject

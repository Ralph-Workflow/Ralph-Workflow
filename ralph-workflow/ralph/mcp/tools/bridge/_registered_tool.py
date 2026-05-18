"""RegisteredTool dataclass."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge._registration_handler import RegistrationHandler
    from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata


@dataclass(frozen=True)
class RegisteredTool:
    """A registered tool and its executable handler."""

    metadata: ToolMetadata
    handler: RegistrationHandler

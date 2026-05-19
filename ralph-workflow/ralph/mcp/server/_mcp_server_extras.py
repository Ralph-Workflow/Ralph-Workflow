"""McpServerExtras — optional runtime extras for start_mcp_server."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy


@dataclass(frozen=True)
class McpServerExtras:
    """Optional runtime extras for start_mcp_server."""

    phase: str | None = None
    extra_env: dict[str, str] | None = None
    restart_policy: McpRestartPolicy | None = None


__all__ = ["McpServerExtras"]

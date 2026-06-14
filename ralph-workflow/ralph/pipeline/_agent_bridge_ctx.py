from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.mcp.server.lifecycle import RestartAwareMcpBridge


@dataclass(frozen=True)
class _AgentBridgeCtx:
    bridge: RestartAwareMcpBridge
    session: object
    system_prompt_file: str

"""Internal context for MCP bridge operations."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.lifecycle import RestartAwareMcpBridge


@dataclass(frozen=True)
class _AgentBridgeCtx:
    bridge: RestartAwareMcpBridge
    session: AgentSession
    system_prompt_file: str

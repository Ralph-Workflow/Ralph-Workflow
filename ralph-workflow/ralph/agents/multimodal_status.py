
from __future__ import annotations

from enum import StrEnum

from ralph.config.agent_transport import AgentTransport


class MultimodalStatus(StrEnum):

    COVERED = "covered"
    EXCLUDED = "excluded"
    NO_MCP = "no_mcp"


_TRANSPORT_STATUS: dict[AgentTransport, MultimodalStatus] = {
    AgentTransport.CLAUDE: MultimodalStatus.COVERED,
    AgentTransport.CLAUDE_INTERACTIVE: MultimodalStatus.COVERED,
    AgentTransport.CODEX: MultimodalStatus.COVERED,
    AgentTransport.OPENCODE: MultimodalStatus.COVERED,
    AgentTransport.NANOCODER: MultimodalStatus.COVERED,
    AgentTransport.AGY: MultimodalStatus.COVERED,
    AgentTransport.PI: MultimodalStatus.COVERED,
    AgentTransport.CURSOR: MultimodalStatus.COVERED,
    AgentTransport.KIMI: MultimodalStatus.COVERED,
    AgentTransport.GENERIC: MultimodalStatus.NO_MCP,
}


def multimodal_status(transport: AgentTransport) -> MultimodalStatus:
    return _TRANSPORT_STATUS[transport]


def all_transport_status_pairs() -> tuple[tuple[AgentTransport, MultimodalStatus], ...]:
    return tuple((t, _TRANSPORT_STATUS[t]) for t in sorted(_TRANSPORT_STATUS, key=str))


__all__ = [
    "MultimodalStatus",
    "all_transport_status_pairs",
    "multimodal_status",
]

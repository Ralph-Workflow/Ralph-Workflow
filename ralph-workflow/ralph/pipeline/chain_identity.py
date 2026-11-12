from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.agents.registry import AgentRegistry
from ralph.mcp.multimodal.capabilities import (
    select_session_transport,
    session_transport_is_ambiguous,
    transport_inline_image_roundtrip_unsafe,
)

if TYPE_CHECKING:
    from ralph.config.enums import AgentTransport
    from ralph.config.models import UnifiedConfig


def phase_session_identity(
    first_agent_transport: AgentTransport | None,
    first_agent_model_flag: str | None,
    chain_transport: AgentTransport | None,
    *,
    chain_is_ambiguous: bool = False,
) -> tuple[AgentTransport | None, str | None]:
    if chain_is_ambiguous:
        return chain_transport or first_agent_transport, None
    if chain_transport is None:
        return first_agent_transport, first_agent_model_flag
    if chain_transport is not first_agent_transport or transport_inline_image_roundtrip_unsafe(
        chain_transport.value
    ):
        return chain_transport, None
    return first_agent_transport, first_agent_model_flag


def chain_disagrees_on_model(model_flags: list[str]) -> bool:
    if len(model_flags) < _MIN_CANDIDATES_TO_DISAGREE:
        return False
    first = model_flags[0]
    return any(flag != first for flag in model_flags)


_MIN_CANDIDATES_TO_DISAGREE = 2


def resolve_phase_session_transport(
    candidate_agents: list[str],
    config: UnifiedConfig | None,
) -> tuple[AgentTransport | None, bool]:
    if config is None or not candidate_agents:
        return None, False
    registry = AgentRegistry.from_config(config)
    by_value: dict[str, AgentTransport] = {}
    ordered: list[str] = []
    model_flags: list[str] = []
    for name in candidate_agents:
        cfg = registry.get(name)
        if cfg is None or cfg.transport is None:
            continue
        ordered.append(cfg.transport.value)
        by_value[cfg.transport.value] = cfg.transport
        model_flags.append((cfg.model_flag or "").strip())
    selected = select_session_transport(ordered)
    ambiguous = session_transport_is_ambiguous(ordered) or chain_disagrees_on_model(model_flags)
    if selected is None:
        return None, ambiguous
    return by_value.get(selected), ambiguous


__all__ = [
    "chain_disagrees_on_model",
    "phase_session_identity",
    "resolve_phase_session_transport",
]

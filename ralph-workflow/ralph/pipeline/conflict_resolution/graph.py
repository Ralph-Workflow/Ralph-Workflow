from __future__ import annotations

from typing import Final

from ralph.policy.resolution_names import RESOLUTION_DRAIN_NAME

PHASE_RESOLUTION: Final[str] = RESOLUTION_DRAIN_NAME
TERMINAL_RESOLVED: Final[str] = "resolved"
TERMINAL_ABANDONED: Final[str] = "abandoned"


def route_after_stop(stops_spent: int, resolved: bool, cap: int) -> str:
    if resolved:
        return TERMINAL_RESOLVED
    if stops_spent >= cap:
        return TERMINAL_ABANDONED
    return PHASE_RESOLUTION


def route_after_round(
    *,
    agent_ran: bool,
    surviving_marker_paths: tuple[str, ...],
    round_index: int,
    cap: int,
) -> str:
    if agent_ran and not surviving_marker_paths:
        return TERMINAL_RESOLVED
    if round_index >= cap:
        return TERMINAL_ABANDONED
    return PHASE_RESOLUTION


__all__ = [
    "PHASE_RESOLUTION",
    "TERMINAL_ABANDONED",
    "TERMINAL_RESOLVED",
    "route_after_round",
    "route_after_stop",
]

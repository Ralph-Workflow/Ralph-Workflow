"""Pure routing decisions for completed conflict-resolution attempts."""

from __future__ import annotations

from typing import Final

PHASE_RESOLUTION: Final[str] = "rebase_conflict_resolution"
TERMINAL_RESOLVED: Final[str] = "resolved"
TERMINAL_ABANDONED: Final[str] = "abandoned"


def route_after_stop(stops_spent: int, resolved: bool, cap: int) -> str:
    """Route after one completed rebase stop using an explicit configured cap."""
    if resolved:
        return TERMINAL_RESOLVED
    if stops_spent >= cap:
        return TERMINAL_ABANDONED
    return PHASE_RESOLUTION


def route_after_round(
    *,
    invocation_succeeded: bool,
    surviving_marker_paths: tuple[str, ...],
    round_index: int,
    cap: int,
) -> str:
    """Route after one completed agent attempt without consulting elapsed time."""
    if invocation_succeeded and not surviving_marker_paths:
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

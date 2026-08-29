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
    agent_ran: bool,
    surviving_marker_paths: tuple[str, ...],
    round_index: int,
    cap: int,
) -> str:
    """Route after one attempt on the evidence, not on the exit status.

    The conflict is resolved when the conflict markers are gone. That is
    the contract the resolver is given in its own prompt -- "Ralph
    Workflow re-scans every listed path textually after you return; your
    own report is never the evidence" -- and it is the only thing that
    is actually true about the repository.

    Requiring the INVOCATION to have succeeded broke that promise from
    the other side: an agent that resolved every marker and then ended
    its session in any way the executor calls unsuccessful had its
    finished work thrown away, the round recorded as a failure, and the
    conflict handed to the next candidate to do again -- until the cap
    ran out and a repaired worktree was abandoned.

    ``agent_ran`` keeps the other half of the rule intact: a round that
    never reached a supervised agent cannot claim the merge is clean,
    however clean the markers look. It is evidence that the work was
    DONE, not merely that a failure was returned.
    """
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

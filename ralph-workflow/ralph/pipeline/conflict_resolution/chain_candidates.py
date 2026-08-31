"""Which agents of the resolution chain a round may still spend.

A candidate can be barred for two different lifetimes, and conflating
them ended conflict resolution outright on the shipped one-agent chain.
A name the agent registry cannot produce will not appear later in the
run, so barring it for the whole run is right. A tool surface that
FAULTED is Ralph's own plumbing -- the recovery layer calls the very
same failure retryable and the MCP breaker that raises it resets itself
-- so that bar lasts only for the current stop.

Split out of :mod:`ralph.pipeline.conflict_resolution.driver`, which
still owns the round that walks the chain.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

__all__ = ["next_unvisited", "remember_dead_surface", "skipped_candidates"]


def skipped_candidates(
    session: ResolutionSession, candidates: tuple[str, ...]
) -> frozenset[str]:
    """Candidates this round must not launch, for either reason."""
    barred = (*session.dead_tool_surfaces, *session.stop_dead_surfaces)
    return frozenset(name for name in candidates if name in barred)


def remember_dead_surface(session: ResolutionSession, agent_name: str) -> None:
    """Bar a candidate, for the run or only for this stop.

    A name the registry cannot produce will not appear mid-run, so that
    bar holds. A tool surface that faulted is Ralph's own plumbing --
    the recovery layer calls the very same failure retryable -- so
    barring the agent for the whole rebase turned one transport hiccup
    into a run that could never resolve anything again, which is exactly
    what the shipped one-agent chain does when its only agent is barred.
    """
    if session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE:
        if agent_name not in session.dead_tool_surfaces:
            session.dead_tool_surfaces = (*session.dead_tool_surfaces, agent_name)
        return
    if agent_name not in session.stop_dead_surfaces:
        session.stop_dead_surfaces = (*session.stop_dead_surfaces, agent_name)


def next_unvisited(offset: int, total: int, visited: set[int]) -> int:
    """Return the next chain position this round has not tried yet."""
    for step in range(1, total + 1):
        candidate = (offset + step) % total
        if candidate not in visited:
            return candidate
    return offset

"""Which agents of the resolution chain a round may still spend.

A candidate can be barred for two different lifetimes, and conflating
them ended conflict resolution outright on the shipped one-agent chain.
A name the agent registry cannot produce will not appear later in the
run, so barring it for the whole run is right. A tool surface that
FAULTED is Ralph's own plumbing -- the recovery layer calls the very
same failure retryable and the MCP breaker that raises it resets itself
-- so that bar lasts only for the current stop.

AGENT HEALTH is a third question, and it has exactly ONE owner:
:class:`~ralph.recovery.agent_failure_ledger.AgentFailureLedger`. The
ledger -- not a counter kept here, not a second copy of the answer --
decides cooldown, demotion and eligibility. This module answers "which
ledger speaks for this session" in exactly one place,
:func:`session_ledger`, so no second code path can reach a different
verdict about the same agent.

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
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger

__all__ = [
    "next_unvisited",
    "remember_dead_surface",
    "session_ledger",
    "skipped_candidates",
]


def session_ledger(session: ResolutionSession | None) -> AgentFailureLedger | None:
    """Return the failure ledger that owns this session's agent health.

    THE single resolution of "which ledger speaks for agent health" in
    conflict resolution: the session's own ledger first, then the
    ledger of the session's recovery controller, then nothing at all.
    Every caller that has to judge whether an agent may run asks THIS
    function, so the chain, the launch seam and the integration seam
    cannot end up reading different ledgers.

    The ledger is the SOLE authority for agent health. ``None`` means
    no ledger could be reached, and that is NOT a second opinion: with
    no authority there is no health verdict to act on, so a caller
    applies no health exclusion at all. The one exclusion that still
    applies without a ledger is the registry-capability one (a name
    the agent registry cannot produce), which is not a health
    judgement.

    Args:
        session: The resolution session whose ledger is wanted. ``None``
            resolves to ``None``.

    Returns:
        The session's failure ledger, or ``None`` when it has none.
    """
    from ralph.recovery.agent_failure_ledger import AgentFailureLedger

    if session is None:
        return None
    ledger = session.ledger
    if isinstance(ledger, AgentFailureLedger):
        return ledger
    controller = session.recovery_controller
    if controller is None:
        return None
    controller_ledger: object = controller.ledger
    if isinstance(controller_ledger, AgentFailureLedger):
        return controller_ledger
    return None


def skipped_candidates(session: ResolutionSession, candidates: tuple[str, ...]) -> frozenset[str]:
    """Candidates this round must not launch.

    Two exclusions, and only two, computed in ONE pass:

    * AGENT HEALTH. The failure ledger is the sole authority for it and
      is consulted FIRST, for every candidate -- including a candidate
      the registry has already excluded. Its verdict is the health
      verdict: a candidate the ledger has spent is barred, and so is a
      candidate the ledger has benched while the session has no
      recovery controller to wait with.
    * REGISTRY CAPABILITY. A name the agent registry cannot produce at
      all will not appear later in the run, so it is barred for the
      whole rebase. This is not a health judgement and it never
      overrides the ledger; it is only added to the health verdict.

    When no ledger can be resolved there is NO health authority, so no
    health exclusion applies: only the registry-capability exclusion
    does.
    """
    from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
    from ralph.recovery.agent_failure_ledger import Spent

    barred: set[str] = set()
    ledger = session_ledger(session)
    if ledger is not None:
        for name in candidates:
            status = ledger.status(PHASE_RESOLUTION, name)
            if isinstance(status, Spent) or (
                session.recovery_controller is None and not status.is_eligible
            ):
                barred.add(name)
    for name in candidates:
        if name in session.dead_tool_surfaces:
            barred.add(name)
    return frozenset(name for name in candidates if name in barred)


def remember_dead_surface(session: ResolutionSession, agent_name: str) -> None:
    """Bar a candidate, for the run or only for this stop.

    When the failure ledger is configured, the ledger is the sole authority
    for agent health and exclusion; dead tool surfaces are only remembered
    when the registry permanently cannot produce the candidate.
    """
    if (
        session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
        and agent_name not in session.dead_tool_surfaces
    ):
        session.dead_tool_surfaces = (*session.dead_tool_surfaces, agent_name)


def next_unvisited(offset: int, total: int, visited: set[int]) -> int:
    """Return the next chain position this round has not tried yet."""
    for step in range(1, total + 1):
        candidate = (offset + step) % total
        if candidate not in visited:
            return candidate
    return offset

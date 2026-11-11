
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
    if (
        session.terminal_reason is ResolutionTerminationReason.CANDIDATE_UNAVAILABLE
        and agent_name not in session.dead_tool_surfaces
    ):
        session.dead_tool_surfaces = (*session.dead_tool_surfaces, agent_name)


def next_unvisited(offset: int, total: int, visited: set[int]) -> int:
    for step in range(1, total + 1):
        candidate = (offset + step) % total
        if candidate not in visited:
            return candidate
    return offset

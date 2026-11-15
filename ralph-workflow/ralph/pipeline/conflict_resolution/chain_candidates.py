from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.conflict_resolution.graph import PHASE_RESOLUTION
from ralph.pipeline.conflict_resolution.session import (
    remember_dead_surface,
    session_ledger,
)

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

__all__ = [
    "next_unvisited",
    "remember_dead_surface",
    "session_ledger",
    "skipped_candidates",
]


def skipped_candidates(session: ResolutionSession, candidates: tuple[str, ...]) -> frozenset[str]:
    from ralph.recovery.agent_failure_ledger import Spent

    barred: set[str] = set()
    ledger = session_ledger(session)
    if ledger is not None:
        for name in candidates:
            status = ledger.status(PHASE_RESOLUTION, name)
            if (
                isinstance(status, Spent)
                or status.reason == "candidate_unavailable"
                or (session.recovery_controller is None and not status.is_eligible)
            ):
                barred.add(name)
    return frozenset(name for name in candidates if name in barred)


def next_unvisited(offset: int, total: int, visited: set[int]) -> int:
    for step in range(1, total + 1):
        candidate = (offset + step) % total
        if candidate not in visited:
            return candidate
    return offset

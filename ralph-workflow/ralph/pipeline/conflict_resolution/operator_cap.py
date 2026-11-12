from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

__all__ = ["MonotonicClock", "operator_cap_expired", "remaining_operator_cap"]

type MonotonicClock = Callable[[], float]


def operator_cap_expired(session: ResolutionSession, clock: MonotonicClock) -> bool:
    cap = session.total_resolution_cap_seconds
    return (
        cap is not None and session.started_at is not None and clock() - session.started_at >= cap
    )


def remaining_operator_cap(
    cap: float | None,
    session: ResolutionSession,
    clock: MonotonicClock,
) -> float | None:
    if cap is None or session.started_at is None:
        return None
    return max(0.0, cap - (clock() - session.started_at))

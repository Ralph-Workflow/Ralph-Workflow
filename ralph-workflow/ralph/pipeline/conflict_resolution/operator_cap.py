"""The rebase-wide resolution deadline an operator can set, and how it is read.

The cap is OFF by default. When it is set it bounds the whole
resolution -- every stop, every round, every candidate -- rather than a
single invocation, so it is measured from the session's start and not
from the current attempt.

Both readings live here because they must agree. Asking "has it expired"
between rounds and "how much is left" immediately before a launch from
two different expressions is how a zero remaining cap reached the
invocation watchdog, whose timeout policy rejects it -- and that
``ValueError`` was then filed as a launch EXCEPTION, naming the agent
for the operator's own deadline.
"""

from __future__ import annotations

from collections.abc import Callable
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.pipeline.conflict_resolution.session import ResolutionSession

__all__ = ["MonotonicClock", "operator_cap_expired", "remaining_operator_cap"]

#: The monotonic time source the cap is measured against. Injected
#: rather than read from :mod:`time` so a test never waits on a real
#: clock. ``Callable`` is imported at RUNTIME because a ``type`` alias
#: body is evaluated lazily and sphinx autodoc forces that evaluation
#: while building the API reference.
type MonotonicClock = Callable[[], float]


def operator_cap_expired(session: ResolutionSession, clock: MonotonicClock) -> bool:
    """Prevent a zero-second cap from reaching an invocation watchdog."""
    cap = session.total_resolution_cap_seconds
    return (
        cap is not None
        and session.started_at is not None
        and clock() - session.started_at >= cap
    )


def remaining_operator_cap(
    cap: float | None,
    session: ResolutionSession,
    clock: MonotonicClock,
) -> float | None:
    """Seconds of the operator's cap still available, or ``None`` for no cap.

    Read immediately before a launch as well as between rounds: one round
    spends every live candidate, so the cap can expire inside it.
    """
    if cap is None or session.started_at is None:
        return None
    return max(0.0, cap - (clock() - session.started_at))

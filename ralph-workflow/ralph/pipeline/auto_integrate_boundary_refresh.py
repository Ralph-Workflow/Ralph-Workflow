"""Throttle for the mainline refresh on dirty phase boundaries.

:func:`ralph.pipeline.auto_integrate.auto_integrate_on_phase_transition`
fires on eleven events per cycle, so an unconditional ``git fetch`` on
every dirty boundary is a real per-cycle cost regression. But a dirty
boundary that reports "nothing to catch up" from a pointer another agent
moved minutes ago is exactly the silent staleness that makes concurrent
multi-agent integration unreliable: the operator-visible skip is computed
from a stale ref and nobody can tell.

A monotonic-clock throttle resolves both: at most one bounded fetch per
interval per process, so the observed pointer is never more than one
interval stale, and the boundary path stays cheap. The clock is injected
so tests advance it directly instead of sleeping.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

#: Minimum wall time between two boundary refreshes. Short enough that a
#: mainline pointer moved by a sibling agent is observed within one
#: phase-boundary cycle, long enough that the eleven boundary events in a
#: cycle collapse to at most one fetch.
DEFAULT_MIN_REFRESH_INTERVAL_SECONDS = 30.0


class BoundaryRefreshThrottle:
    """Rate-limits the origin refresh performed on dirty phase boundaries."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = DEFAULT_MIN_REFRESH_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        """Build a throttle.

        Args:
            min_interval_seconds: Minimum seconds between two permitted
                refreshes.
            clock: Monotonic time source, injected for tests. Never a
                wall clock: a system clock adjustment must not be able to
                suppress or force a refresh.
        """
        self._min_interval_seconds = min_interval_seconds
        self._clock = clock
        self._last_refresh: float | None = None

    def should_refresh(self) -> bool:
        """Whether a refresh is permitted now, recording it when it is.

        Returns:
            ``True`` on the first call and thereafter only once
            ``min_interval_seconds`` has elapsed since the last permitted
            refresh. The timestamp advances only when ``True`` is
            returned, so a suppressed call does not extend the window.
        """
        now = self._clock()
        last = self._last_refresh
        if last is not None and now - last < self._min_interval_seconds:
            return False
        self._last_refresh = now
        return True


#: Process-wide throttle shared by every phase-boundary hook, so the
#: eleven boundary events of one cycle share a single budget. Tests must
#: construct their own :class:`BoundaryRefreshThrottle` with a fake clock
#: rather than mutating this singleton.
BOUNDARY_REFRESH_THROTTLE = BoundaryRefreshThrottle()

__all__ = [
    "BOUNDARY_REFRESH_THROTTLE",
    "DEFAULT_MIN_REFRESH_INTERVAL_SECONDS",
    "BoundaryRefreshThrottle",
]

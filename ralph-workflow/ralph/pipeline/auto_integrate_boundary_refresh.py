"""Throttle for the mainline refresh on dirty phase boundaries.

:func:`ralph.pipeline.auto_integrate.auto_integrate_on_phase_transition`
fires on eleven events per cycle, so an unconditional ``git fetch`` on
every dirty boundary is a real per-cycle cost regression. But a dirty
boundary that reports "nothing to catch up" from a pointer another agent
moved minutes ago is exactly the silent staleness that makes concurrent
multi-agent integration unreliable: the operator-visible skip is computed
from a stale ref and nobody can tell.

A monotonic-clock throttle resolves both, under two rules that exist
because breaking either of them re-creates the staleness the throttle was
meant to bound:

* **Keyed per (repository root, target branch).** The window is not a
  process-global scalar. Several workspace scopes -- linked worktrees of
  one fleet, or several manifest-launched parallel workers -- share one
  process, and a scalar window let whichever of them probed first steal
  the whole interval from all the others.
* **Consumed only on a SUCCESSFUL refresh.** :meth:`should_refresh`
  grants permission; :meth:`record_outcome` arms the window, and only
  for an outcome that :func:`~ralph.pipeline.auto_integrate_context
  .refresh_outcome_is_healthy` accepts. A refresh that came back
  ``REFRESH_UNREACHABLE`` established no freshness at all, so letting it
  burn the interval guaranteed at least one whole window of unrefreshed
  boundary probes after every transient blip.

The clock is injected so tests advance it directly instead of sleeping.
"""

from __future__ import annotations

import time
from collections import OrderedDict
from typing import TYPE_CHECKING

from ralph.pipeline.auto_integrate_context import refresh_outcome_is_healthy

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

#: Minimum wall time between two boundary refreshes. Short enough that a
#: mainline pointer moved by a sibling agent is observed within one
#: phase-boundary cycle, long enough that the eleven boundary events in a
#: cycle collapse to at most one fetch.
DEFAULT_MIN_REFRESH_INTERVAL_SECONDS = 30.0

#: Hard cap on the number of tracked ``(root, target)`` windows. The map
#: is a long-lived mutable accumulator on ``self``, so
#: ``ralph/testing/audit_resource_lifecycle.py`` requires an explicit
#: bound: entries are evicted FIFO once this many keys are live. A fleet
#: runs a handful of worktrees against one or two targets, so the cap is
#: never reached in practice -- it exists so a pathological caller
#: cannot grow the map without limit across a long unattended run.
_MAX_TRACKED_KEYS = 64


class BoundaryRefreshThrottle:
    """Rate-limits the origin refresh performed on dirty phase boundaries."""

    def __init__(
        self,
        *,
        min_interval_seconds: float = DEFAULT_MIN_REFRESH_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_tracked_keys: int = _MAX_TRACKED_KEYS,
    ) -> None:
        """Build a throttle.

        Args:
            min_interval_seconds: Minimum seconds between two successful
                refreshes of one ``(root, target)`` pair.
            clock: Monotonic time source, injected for tests. Never a
                wall clock: a system clock adjustment must not be able to
                suppress or force a refresh.
            max_tracked_keys: FIFO cap on the number of live
                ``(root, target)`` windows.
        """
        self._min_interval_seconds = min_interval_seconds
        self._clock = clock
        self._max_tracked_keys = max(1, max_tracked_keys)
        self._last_refresh: OrderedDict[  # bounded-accumulator-ok: FIFO-capped in _arm
            tuple[str, str], float
        ] = OrderedDict()

    def should_refresh(self, root: Path | str, target: str) -> bool:
        """Whether a refresh of ``target`` under ``root`` is permitted now.

        Permission is granted when this pair has no armed window, or when
        ``min_interval_seconds`` has elapsed since its last SUCCESSFUL
        refresh. Asking does not consume anything: the caller must report
        back through :meth:`record_outcome`, and only a healthy outcome
        arms the next window.

        Args:
            root: Repository root the refresh would run in.
            target: Target branch the refresh would freshen.

        Returns:
            ``True`` when the caller may refresh now.
        """
        last = self._last_refresh.get(self._key(root, target))
        if last is None:
            return True
        return self._clock() - last >= self._min_interval_seconds

    def record_outcome(self, root: Path | str, target: str, outcome: str) -> None:
        """Arm the next window for ``(root, target)`` if ``outcome`` was healthy.

        An outcome that could not vouch for the pointer (an unreachable
        origin, a lost race, a failed query) leaves the window unarmed,
        so the very next boundary probe is permitted to try again rather
        than inheriting a whole interval of blindness from one blip.

        Args:
            root: Repository root the refresh ran in.
            target: Target branch the refresh tried to freshen.
            outcome: The ``REFRESH_*`` outcome the refresh returned.
        """
        if not refresh_outcome_is_healthy(outcome):
            return
        self._arm(self._key(root, target))

    @staticmethod
    def _key(root: Path | str, target: str) -> tuple[str, str]:
        """Normalise a ``(root, target)`` pair into a hashable map key."""
        return (str(root), target)

    def _arm(self, key: tuple[str, str]) -> None:
        """Start a fresh window for ``key``, evicting the oldest if capped."""
        self._last_refresh.pop(key, None)
        while len(self._last_refresh) >= self._max_tracked_keys:
            self._last_refresh.popitem(last=False)
        self._last_refresh[key] = self._clock()


#: Process-wide throttle shared by every phase-boundary hook, so the
#: eleven boundary events of one cycle share a single budget PER
#: ``(root, target)`` pair. Tests must construct their own
#: :class:`BoundaryRefreshThrottle` with a fake clock rather than
#: mutating this singleton.
BOUNDARY_REFRESH_THROTTLE = BoundaryRefreshThrottle()


__all__ = [
    "BOUNDARY_REFRESH_THROTTLE",
    "DEFAULT_MIN_REFRESH_INTERVAL_SECONDS",
    "BoundaryRefreshThrottle",
]

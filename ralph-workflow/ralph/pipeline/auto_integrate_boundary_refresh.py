from __future__ import annotations

import time
from collections import OrderedDict
from typing import TYPE_CHECKING

from ralph.pipeline.auto_integrate_context import refresh_outcome_is_healthy

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

DEFAULT_MIN_REFRESH_INTERVAL_SECONDS = 30.0

_MAX_TRACKED_KEYS = 64


class BoundaryRefreshThrottle:
    def __init__(
        self,
        *,
        min_interval_seconds: float = DEFAULT_MIN_REFRESH_INTERVAL_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        max_tracked_keys: int = _MAX_TRACKED_KEYS,
    ) -> None:
        self._min_interval_seconds = min_interval_seconds
        self._clock = clock
        self._max_tracked_keys = max(1, max_tracked_keys)
        self._last_refresh: OrderedDict[tuple[str, str], tuple[float, float | None]] = OrderedDict()

    def should_refresh(self, root: Path | str, target: str) -> bool:
        timestamps = self._last_refresh.get(self._key(root, target))
        if timestamps is None:
            return True
        return self._clock() - timestamps[0] >= self._min_interval_seconds

    def should_force_refresh(self, root: Path | str, target: str) -> bool:
        timestamps = self._last_refresh.get(self._key(root, target))
        if timestamps is None or timestamps[1] is None:
            return True
        return self._clock() - timestamps[1] >= self._min_interval_seconds

    def record_outcome(self, root: Path | str, target: str, outcome: str) -> None:
        if not refresh_outcome_is_healthy(outcome):
            return
        self._arm(self._key(root, target), forced_at=None)

    def record_forced_outcome(self, root: Path | str, target: str, outcome: str) -> None:
        if not refresh_outcome_is_healthy(outcome):
            return
        now = self._clock()
        self._arm(self._key(root, target), refreshed_at=now, forced_at=now)

    @staticmethod
    def _key(root: Path | str, target: str) -> tuple[str, str]:
        return (str(root), target)

    def _arm(
        self,
        key: tuple[str, str],
        *,
        refreshed_at: float | None = None,
        forced_at: float | None,
    ) -> None:
        self._last_refresh.pop(key, None)
        while len(self._last_refresh) >= self._max_tracked_keys:
            self._last_refresh.popitem(last=False)
        self._last_refresh[key] = (
            self._clock() if refreshed_at is None else refreshed_at,
            forced_at,
        )


BOUNDARY_REFRESH_THROTTLE = BoundaryRefreshThrottle()


__all__ = [
    "BOUNDARY_REFRESH_THROTTLE",
    "DEFAULT_MIN_REFRESH_INTERVAL_SECONDS",
    "BoundaryRefreshThrottle",
]

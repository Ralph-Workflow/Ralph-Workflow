"""Sustained-suspicion tracker for §3b fuzzy liveness."""

from __future__ import annotations

from ralph import timeout_defaults as _td

_SUSPICIOUS_RULES: frozenset[str] = frozenset({"R4", "R7"})


class SuspicionClock:
    """Sustained-suspicion tracker for R4 or R7 dominant verdict."""

    __slots__ = ("_streak_s", "_threshold_s")

    def __init__(self, *, threshold_s: float | None = None) -> None:
        threshold = _td.EXEC_SUSPICIOUS_REPORT_S if threshold_s is None else threshold_s
        if threshold <= 0.0:
            raise ValueError(f"threshold_s must be positive; got {threshold!r}")
        self._threshold_s = float(threshold)
        self._streak_s = 0.0

    def step(self, *, strongest_rule: str, dt: float) -> bool:
        """Advance the clock by ``dt`` and return ``True`` iff sustained."""
        if dt < 0.0:
            raise ValueError(f"dt must be non-negative; got {dt!r}")
        if strongest_rule in _SUSPICIOUS_RULES:
            self._streak_s += dt
        else:
            self._streak_s = 0.0
        return self._streak_s >= self._threshold_s

    @property
    def streak_s(self) -> float:
        return self._streak_s

    @property
    def threshold_s(self) -> float:
        return self._threshold_s

    @property
    def is_sustained(self) -> bool:
        return self._streak_s >= self._threshold_s

    def reset(self) -> None:
        self._streak_s = 0.0

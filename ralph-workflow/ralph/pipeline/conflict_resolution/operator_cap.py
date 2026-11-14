from __future__ import annotations

from collections.abc import Callable

__all__ = ["MonotonicClock"]

type MonotonicClock = Callable[[], float]

from __future__ import annotations


class FakeClock:
    def __init__(self, initial_time: float = 1000.0) -> None:
        self._time = initial_time

    def now(self) -> float:
        return self._time

    def sleep(self, seconds: float) -> None:
        self._time += max(0.0, seconds)

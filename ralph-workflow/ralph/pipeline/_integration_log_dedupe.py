
from __future__ import annotations

import threading
import time
from collections import OrderedDict
from collections.abc import Callable

type OutcomeEmit = Callable[[str], None]
type MonotonicClock = Callable[[], float]


class IntegrationOutcomeLog:

    def __init__(
        self,
        *,
        clock: MonotonicClock = time.monotonic,
        repeat_log_interval_seconds: float = 300.0,
        max_entries: int = 64,
    ) -> None:
        if repeat_log_interval_seconds <= 0:
            raise ValueError("repeat_log_interval_seconds must be positive")
        if max_entries <= 0:
            raise ValueError("max_entries must be positive")
        self._clock = clock
        self._repeat_log_interval_seconds = repeat_log_interval_seconds
        self._max_entries = max_entries
        self._records: OrderedDict[str, tuple[float, int]] = (
            OrderedDict()
        )
        self._lock = threading.Lock()

    def record(self, emit: OutcomeEmit, message: str) -> bool:
        now = self._clock()
        summary: str | None = None
        with self._lock:
            entry = self._records.get(message)
            if entry is None:
                self._records[message] = (now, 1)
                self._records.move_to_end(message)
                self._evict_oldest()
                summary = message
            else:
                last_emitted_at, count = entry
                repeats = count + 1
                if now - last_emitted_at < self._repeat_log_interval_seconds:
                    self._records[message] = (last_emitted_at, repeats)
                    return False
                self._records[message] = (now, 1)
                self._records.move_to_end(message)
                summary = f"{message} (repeated {repeats}x since the last update)"
        emit(summary)
        return True

    def _evict_oldest(self) -> None:
        while len(self._records) > self._max_entries:
            self._records.popitem(last=False)


_default_outcome_log = IntegrationOutcomeLog()


def integration_outcome_log() -> IntegrationOutcomeLog:
    return _default_outcome_log


__all__ = ["IntegrationOutcomeLog", "integration_outcome_log"]

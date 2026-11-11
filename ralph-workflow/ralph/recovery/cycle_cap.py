
from __future__ import annotations


class CycleCap:

    def __init__(self, cap: int) -> None:
        if cap < 1:
            raise ValueError(f"CycleCap must be >= 1, got {cap}")
        self._cap = cap

    @property
    def cap(self) -> int:
        return self._cap

    def is_exceeded(self, count: int) -> bool:
        return count >= self._cap

    def exit_reason(self, count: int, last_category: str, last_reason: str) -> str:
        return (
            f"recovery cycle cap {self._cap} exceeded after {count} recoveries; "
            f"last failure category={last_category}: {last_reason}"
        )

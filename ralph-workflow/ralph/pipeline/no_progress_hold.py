from __future__ import annotations

from dataclasses import dataclass
from typing import Final

NO_PROGRESS_REPEATS: Final[int] = 3
NO_PROGRESS_BASE_WAIT_SECONDS: Final[float] = 30.0
NO_PROGRESS_MAX_WAIT_SECONDS: Final[float] = 600.0
NO_PROGRESS_RELEASE_REPEATS: Final[int] = 6


@dataclass
class NoProgressHoldBackoff:
    signature: tuple[str, ...] | None = None
    repeats: int = 0

    def observe(self, signature: tuple[str, ...]) -> float:
        if signature == self.signature:
            self.repeats += 1
        else:
            self.signature = signature
            self.repeats = 1
        if self.repeats < NO_PROGRESS_REPEATS:
            return 0.0
        doublings = self.repeats - NO_PROGRESS_REPEATS
        return min(
            NO_PROGRESS_BASE_WAIT_SECONDS * float(1 << min(doublings, 16)),
            NO_PROGRESS_MAX_WAIT_SECONDS,
        )

    def reset(self) -> None:
        self.signature = None
        self.repeats = 0


__all__ = [
    "NO_PROGRESS_BASE_WAIT_SECONDS",
    "NO_PROGRESS_MAX_WAIT_SECONDS",
    "NO_PROGRESS_RELEASE_REPEATS",
    "NO_PROGRESS_REPEATS",
    "NoProgressHoldBackoff",
]

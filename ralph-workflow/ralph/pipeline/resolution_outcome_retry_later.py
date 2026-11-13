from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RetryLater"]


@dataclass(frozen=True)
class RetryLater:
    until: float | None = None
    on_change: str | None = None

    def __post_init__(self) -> None:
        if (self.until is None) == (self.on_change is None):
            raise ValueError("exactly one of until or on_change is required")

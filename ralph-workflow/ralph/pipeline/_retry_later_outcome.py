from __future__ import annotations

from dataclasses import dataclass

__all__ = ["RetryLaterOutcome"]


@dataclass(frozen=True, slots=True)
class RetryLaterOutcome:
    until: float | None = None
    on_change: str | None = None
    succeeded: bool = False
    reason: object = None
    unresolved_paths: tuple[str, ...] = ()

    def __bool__(self) -> bool:
        return False

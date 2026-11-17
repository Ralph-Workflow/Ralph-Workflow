from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.git.resolution_failure_cause import ResolutionFailureCause

__all__ = ["RetryLaterOutcome"]


@dataclass(frozen=True, slots=True)
class RetryLaterOutcome:
    until: float | None = None
    on_change: str | None = None
    terminal_reason: object | None = None
    unresolved_paths: tuple[str, ...] = ()
    failure_cause: ResolutionFailureCause | None = None

    def __post_init__(self) -> None:
        if (self.until is None) == (self.on_change is None):
            raise ValueError("RetryLaterOutcome requires exactly one of until or on_change")

    @property
    def succeeded(self) -> bool:
        return False

    @property
    def reason(self) -> object:
        if self.on_change is not None:
            return self.on_change
        if self.until is not None:
            return f"retry after {self.until}"
        return self.terminal_reason

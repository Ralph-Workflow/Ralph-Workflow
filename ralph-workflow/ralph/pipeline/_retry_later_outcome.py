from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

    from ralph.git.resolution_failure_cause import ResolutionFailureCause

__all__ = ["RetryLaterOutcome"]

_TUPLE_PAIR_LEN = 2


@dataclass(frozen=True, slots=True)
class RetryLaterOutcome:
    until: float | None = None
    on_change: str | None = None
    terminal_reason: object | None = None
    unresolved_paths: tuple[str, ...] = ()
    terminal_reason: object | None = None
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

    def __iter__(self) -> Iterator[object]:
        return iter((False, self.on_change or ""))

    def __getitem__(self, index: int) -> object:
        return (False, self.on_change or "")[index]

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return False

    def __hash__(self) -> int:
        return hash(
            (self.until, self.on_change, self.terminal_reason, self.unresolved_paths)
        )

    def __eq__(self, other: object) -> bool:
        if (
            isinstance(other, tuple)
            and len(other) == _TUPLE_PAIR_LEN
            and isinstance(other[0], bool)
            and isinstance(other[1], str)
        ):
            return other == (False, self.on_change or "")
        return (
            isinstance(other, RetryLaterOutcome)
            and (self.until, self.on_change) == (other.until, other.on_change)
            and (self.terminal_reason, self.unresolved_paths)
            == (other.terminal_reason, other.unresolved_paths)
        )

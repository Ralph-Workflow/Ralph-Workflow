from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["RetryLaterOutcome"]

_TUPLE_PAIR_LEN = 2


class _RetryLaterOutcomeMeta(type):
    def __instancecheck__(cls, instance: object) -> bool:
        if super().__instancecheck__(instance):
            return True
        from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome

        return isinstance(instance, ResolutionOutcome) and not instance.succeeded


@dataclass(frozen=True, slots=True)
class RetryLaterOutcome(metaclass=_RetryLaterOutcomeMeta):
    until: float | None = None
    on_change: str | None = None
    succeeded: bool = False
    reason: object = None
    unresolved_paths: tuple[str, ...] = ()

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
            (self.until, self.on_change, self.succeeded, self.reason, self.unresolved_paths)
        )

    def __iter__(self) -> Iterator[object]:
        return iter((False, self.on_change or ""))

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
            and (self.succeeded, self.reason, self.unresolved_paths)
            == (other.succeeded, other.reason, other.unresolved_paths)
        )


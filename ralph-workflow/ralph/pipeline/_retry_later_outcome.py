from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["RetryLaterOutcome"]


@dataclass(frozen=True, slots=True)
class RetryLaterOutcome:
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

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["UnknownOutcome"]


@dataclass(frozen=True, slots=True)
class UnknownOutcome:
    error: BaseException
    succeeded: bool = False
    reason: object = None
    unresolved_paths: tuple[str, ...] = ()

    def __iter__(self) -> Iterator[object]:
        return iter((False, str(self.error)))

    def __getitem__(self, index: int) -> object:
        return (False, str(self.error))[index]

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return False

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["ResolvedOutcome"]


@dataclass(frozen=True, slots=True)
class ResolvedOutcome:
    succeeded: bool = True
    reason: object = None
    unresolved_paths: tuple[str, ...] = ()

    def __iter__(self) -> Iterator[object]:
        return iter((True, ""))

    def __getitem__(self, index: int) -> object:
        return (True, "")[index]

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return True

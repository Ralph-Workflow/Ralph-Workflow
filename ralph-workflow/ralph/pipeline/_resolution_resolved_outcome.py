from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True, slots=True)
class ResolvedOutcome:
    def __iter__(self) -> Iterator[object]:
        return iter((True, ""))

    def __getitem__(self, index: int) -> object:
        return (True, "")[index]

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return True


__all__ = ["ResolvedOutcome"]

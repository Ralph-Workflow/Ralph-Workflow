from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

__all__ = ["ConfigFailureOutcome"]


@dataclass(frozen=True, slots=True)
class ConfigFailureOutcome:
    file: Path
    parse_error: BaseException | str
    succeeded: bool = False
    reason: object = None
    unresolved_paths: tuple[str, ...] = ()

    def __iter__(self) -> Iterator[object]:
        return iter((False, str(self.parse_error)))

    def __getitem__(self, index: int) -> object:
        return (False, str(self.parse_error))[index]

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return False

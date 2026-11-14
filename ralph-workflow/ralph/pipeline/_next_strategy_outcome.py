from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["NextStrategyOutcome"]


@dataclass(frozen=True, slots=True)
class NextStrategyOutcome:
    strategy: str
    terminal_reason: object | None = None
    unresolved_paths: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return False

    @property
    def reason(self) -> object:
        return self.terminal_reason

    def __iter__(self) -> Iterator[object]:
        return iter((False, self.strategy))

    def __getitem__(self, index: int) -> object:
        return (False, self.strategy)[index]

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return False

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


@dataclass(frozen=True, slots=True)
class WaitingOutcome:
    external_condition: str
    resumes_when: str

    def __iter__(self) -> Iterator[object]:
        return iter((False, self.external_condition or ""))

    def __getitem__(self, index: int) -> object:
        return (False, self.external_condition or "")[index]

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return False


__all__ = ["WaitingOutcome"]

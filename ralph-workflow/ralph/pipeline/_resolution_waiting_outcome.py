from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


_TUPLE_LEN = 2


@dataclass(frozen=True, slots=True)
class WaitingOutcome:
    external_condition: str
    resumes_when: str

    @property
    def succeeded(self) -> bool:
        return False

    @property
    def reason(self) -> str:
        return self.external_condition

    def __bool__(self) -> bool:
        return False

    def __iter__(self) -> Iterator[bool | str]:
        yield self.succeeded
        yield self.reason

    def __getitem__(self, index: int) -> object:
        return (self.succeeded, self.reason)[index]

    def __len__(self) -> int:
        return _TUPLE_LEN

    def __eq__(self, other: object) -> bool:
        if isinstance(other, tuple) and len(other) == _TUPLE_LEN:
            return other == (False, self.external_condition)
        if isinstance(other, WaitingOutcome):
            return (self.external_condition, self.resumes_when) == (
                other.external_condition,
                other.resumes_when,
            )
        return False

    def __hash__(self) -> int:
        return hash((self.external_condition, self.resumes_when))


__all__ = ["WaitingOutcome"]

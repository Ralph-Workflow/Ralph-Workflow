from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator


_TUPLE_LEN = 2


@dataclass(frozen=True, slots=True)
class ResolvedOutcome:
    @property
    def succeeded(self) -> bool:
        return True

    @property
    def reason(self) -> str:
        return ""

    def __bool__(self) -> bool:
        return True

    def __iter__(self) -> Iterator[bool | str]:
        yield self.succeeded
        yield self.reason

    def __getitem__(self, index: int) -> object:
        return (self.succeeded, self.reason)[index]

    def __len__(self) -> int:
        return _TUPLE_LEN

    def __eq__(self, other: object) -> bool:
        if isinstance(other, tuple) and len(other) == _TUPLE_LEN:
            return other == (True, "")
        return isinstance(other, ResolvedOutcome)

    def __hash__(self) -> int:
        return hash((True, ""))


__all__ = ["ResolvedOutcome"]

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = ["WaitingOutcome"]

_TUPLE_PAIR_LEN = 2


@dataclass(frozen=True, slots=True)
class WaitingOutcome:
    external_condition: str
    resumes_when: str
    terminal_reason: object | None = None
    unresolved_paths: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return False

    @property
    def reason(self) -> object:
        return self.terminal_reason
    def __iter__(self) -> Iterator[object]:
        return iter((False, self.external_condition))

    def __getitem__(self, index: int) -> object:
        return (False, self.external_condition)[index]

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return False

    def __hash__(self) -> int:
        return hash(
            (
                self.external_condition,
                self.resumes_when,
                self.succeeded,
                self.reason,
                self.unresolved_paths,
            )
        )

    def __iter__(self) -> Iterator[object]:
        return iter((False, self.external_condition or ""))

    def __eq__(self, other: object) -> bool:
        if (
            isinstance(other, tuple)
            and len(other) == _TUPLE_PAIR_LEN
            and isinstance(other[0], bool)
            and isinstance(other[1], str)
        ):
            return other == (False, self.external_condition or "")
        return (
            isinstance(other, WaitingOutcome)
            and (self.external_condition, self.resumes_when)
            == (other.external_condition, other.resumes_when)
            and (self.succeeded, self.reason, self.unresolved_paths)
            == (other.succeeded, other.reason, other.unresolved_paths)
        )


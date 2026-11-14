from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


_TUPLE_LEN = 2


@dataclass(frozen=True, slots=True)
class ConfigFailureOutcome:
    file: Path
    parse_error: str

    @classmethod
    def from_error(cls, error: BaseException) -> ConfigFailureOutcome:
        from ralph.config.loader import ConfigLoadError

        if not isinstance(error, ConfigLoadError):
            raise TypeError("ConfigFailureOutcome requires ConfigLoadError")
        return cls(file=error.file, parse_error=error.parse_error)

    @property
    def succeeded(self) -> bool:
        return False

    @property
    def reason(self) -> str:
        return f"fix {self.file} then run ralph --check-config"

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
            return other == (False, f"fix {self.file} then run ralph --check-config")
        if isinstance(other, ConfigFailureOutcome):
            return (self.file, self.parse_error) == (other.file, other.parse_error)
        return False

    def __hash__(self) -> int:
        return hash((self.file, self.parse_error))


__all__ = ["ConfigFailureOutcome"]

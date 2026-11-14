from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path


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

    def __iter__(self) -> Iterator[object]:
        return iter((False, f"config failure: {self.file}: {self.parse_error}"))

    def __getitem__(self, index: int) -> object:
        return (False, f"config failure: {self.file}: {self.parse_error}")[index]

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return False


__all__ = ["ConfigFailureOutcome"]

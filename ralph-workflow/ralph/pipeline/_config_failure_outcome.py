from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["ConfigFailureOutcome"]


@dataclass(frozen=True, slots=True)
class ConfigFailureOutcome:
    file: Path
    parse_error: BaseException | str

    @classmethod
    def from_error(cls, error: BaseException) -> ConfigFailureOutcome:
        from ralph.config.loader import ConfigLoadError

        if not isinstance(error, ConfigLoadError):
            raise TypeError("ConfigFailureOutcome requires ConfigLoadError")
        return cls(file=error.file, parse_error=error.parse_error)

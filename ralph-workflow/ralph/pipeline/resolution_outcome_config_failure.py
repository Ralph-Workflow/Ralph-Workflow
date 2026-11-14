from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.config._toml_fallback import ConfigParseError

__all__ = ["ConfigFailure"]


@dataclass(frozen=True, slots=True)
class ConfigFailure:
    file: Path
    parse_error: BaseException

    @classmethod
    def from_payload(cls, payload: ConfigParseError) -> ConfigFailure:
        return cls(file=payload.file, parse_error=payload.parse_error)

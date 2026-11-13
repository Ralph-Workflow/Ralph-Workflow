from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

from ralph.config._toml_fallback import ConfigParseError, config_parse_error

__all__ = ["ConfigFailure", "config_failure"]


@dataclass(frozen=True)
class ConfigFailure:
    file: Path
    parse_error: BaseException

    @classmethod
    def from_payload(cls, payload: ConfigParseError) -> ConfigFailure:
        return cls(file=payload.file, parse_error=payload.parse_error)


def config_failure(file: Path | str, parse_error: BaseException) -> ConfigFailure:
    return ConfigFailure.from_payload(config_parse_error(file, parse_error))

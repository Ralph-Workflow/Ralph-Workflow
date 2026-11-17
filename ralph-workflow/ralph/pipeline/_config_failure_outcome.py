from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.git.resolution_failure_cause import ResolutionFailureCause

__all__ = ["ConfigFailureOutcome"]


@dataclass(frozen=True, slots=True)
class ConfigFailureOutcome:
    file: Path
    parse_error: BaseException | str
    terminal_reason: object | None = None
    unresolved_paths: tuple[str, ...] = ()

    @property
    def succeeded(self) -> bool:
        return False

    @property
    def reason(self) -> object:
        return self.terminal_reason

    @property
    def failure_cause(self) -> ResolutionFailureCause:
        from ralph.git.resolution_failure_cause import ResolutionFailureCause

        return ResolutionFailureCause.CONFIG_FAILURE

    @classmethod
    def from_error(cls, error: object) -> ConfigFailureOutcome:
        from ralph.config.loader import ConfigLoadError

        if not isinstance(error, ConfigLoadError):
            raise TypeError("ConfigFailureOutcome requires ConfigLoadError")
        return cls(file=error.file, parse_error=error.parse_error)

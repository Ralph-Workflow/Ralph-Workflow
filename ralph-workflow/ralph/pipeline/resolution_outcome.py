from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from ralph.pipeline.key_unavailable import KeyUnavailable

if TYPE_CHECKING:
    from pathlib import Path

__all__ = [
    "ConfigFailureOutcome",
    "KeyUnavailable",
    "NextStrategyOutcome",
    "ResolvedOutcome",
    "RetryLaterOutcome",
    "StopResolutionOutcome",
    "UnknownOutcome",
    "WaitingOutcome",
    "assert_never",
]


@dataclass(frozen=True, slots=True)
class ResolvedOutcome:
    pass


@dataclass(frozen=True, slots=True)
class WaitingOutcome:
    external_condition: str
    resumes_when: str


@dataclass(frozen=True, slots=True)
class RetryLaterOutcome:
    until: float | None = None
    on_change: str | None = None

    def __post_init__(self) -> None:
        if (self.until is None) == (self.on_change is None):
            raise ValueError("RetryLaterOutcome requires exactly one of until or on_change")


@dataclass(frozen=True, slots=True)
class NextStrategyOutcome:
    strategy: str


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


@dataclass(frozen=True, slots=True)
class UnknownOutcome:
    error: BaseException


type StopResolutionOutcome = (
    ResolvedOutcome
    | WaitingOutcome
    | RetryLaterOutcome
    | NextStrategyOutcome
    | ConfigFailureOutcome
    | UnknownOutcome
)

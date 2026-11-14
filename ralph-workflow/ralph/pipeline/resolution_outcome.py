from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, assert_never

from ralph.pipeline._config_failure_outcome import ConfigFailureOutcome
from ralph.pipeline._next_strategy_outcome import NextStrategyOutcome
from ralph.pipeline._resolved_outcome import ResolvedOutcome
from ralph.pipeline._unknown_outcome import UnknownOutcome
from ralph.pipeline._waiting_outcome import WaitingOutcome
from ralph.pipeline.key_unavailable import KeyUnavailable

if TYPE_CHECKING:
    from collections.abc import Iterator

__all__ = [
    "ConfigFailureOutcome",
    "KeyUnavailable",
    "NextStrategyOutcome",
    "ResolutionOutcomeUnion",
    "ResolvedOutcome",
    "RetryLaterOutcome",
    "StopResolutionOutcome",
    "UnknownOutcome",
    "WaitingOutcome",
    "assert_never",
]


@dataclass(frozen=True, slots=True)
class RetryLaterOutcome:
    until: float | None = None
    on_change: str | None = None

    def __post_init__(self) -> None:
        if (self.until is None) == (self.on_change is None):
            raise ValueError("RetryLaterOutcome requires exactly one of until or on_change")

    def __iter__(self) -> Iterator[object]:
        return iter((False, self.on_change or ""))

    def __getitem__(self, index: int) -> object:
        return (False, self.on_change or "")[index]

    def __len__(self) -> int:
        return 2

    def __bool__(self) -> bool:
        return False


ResolutionOutcomeUnion = (
    ResolvedOutcome
    | WaitingOutcome
    | RetryLaterOutcome
    | NextStrategyOutcome
    | ConfigFailureOutcome
    | UnknownOutcome
)

type StopResolutionOutcome = ResolutionOutcomeUnion

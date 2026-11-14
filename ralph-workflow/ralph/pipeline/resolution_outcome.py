from __future__ import annotations

from dataclasses import dataclass
from typing import assert_never

from ralph.pipeline._resolution_config_failure_outcome import ConfigFailureOutcome
from ralph.pipeline._resolution_next_strategy_outcome import NextStrategyOutcome
from ralph.pipeline._resolution_resolved_outcome import ResolvedOutcome
from ralph.pipeline._resolution_unknown_outcome import UnknownOutcome
from ralph.pipeline._resolution_waiting_outcome import WaitingOutcome
from ralph.pipeline.key_unavailable import KeyUnavailable

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
class RetryLaterOutcome:
    until: float | None = None
    on_change: str | None = None

    def __post_init__(self) -> None:
        if (self.until is None) == (self.on_change is None):
            raise ValueError("RetryLaterOutcome requires exactly one of until or on_change")


type StopResolutionOutcome = (
    ResolvedOutcome
    | WaitingOutcome
    | RetryLaterOutcome
    | NextStrategyOutcome
    | ConfigFailureOutcome
    | UnknownOutcome
)

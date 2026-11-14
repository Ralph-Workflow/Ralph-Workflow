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
    "stop_resolution_outcome_reason",
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


def stop_resolution_outcome_reason(outcome: StopResolutionOutcome) -> str:
    match outcome:
        case ResolvedOutcome():
            return ""
        case WaitingOutcome(external_condition=cond):
            return cond
        case RetryLaterOutcome(on_change=change):
            return change or ""
        case NextStrategyOutcome(strategy=strat):
            return strat
        case ConfigFailureOutcome(parse_error=err):
            return str(err)
        case UnknownOutcome(error=err):
            return str(err)
        case _ as unreachable:
            assert_never(unreachable)

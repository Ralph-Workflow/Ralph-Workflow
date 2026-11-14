from __future__ import annotations

from typing import assert_never

from ralph.pipeline._config_failure_outcome import ConfigFailureOutcome
from ralph.pipeline._next_strategy_outcome import NextStrategyOutcome
from ralph.pipeline._resolved_outcome import ResolvedOutcome
from ralph.pipeline._retry_later_outcome import RetryLaterOutcome
from ralph.pipeline._unknown_outcome import UnknownOutcome
from ralph.pipeline._waiting_outcome import WaitingOutcome
from ralph.pipeline.key_unavailable import KeyUnavailable

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

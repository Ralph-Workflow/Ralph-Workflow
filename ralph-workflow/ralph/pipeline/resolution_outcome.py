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

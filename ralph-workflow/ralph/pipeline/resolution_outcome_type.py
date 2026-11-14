from __future__ import annotations

from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    UnknownOutcome,
    WaitingOutcome,
)

__all__ = ["ResolutionOutcomeType"]

type ResolutionOutcomeType = (
    ResolvedOutcome
    | WaitingOutcome
    | RetryLaterOutcome
    | NextStrategyOutcome
    | ConfigFailureOutcome
    | UnknownOutcome
)

from __future__ import annotations

from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    UnknownOutcome,
    WaitingOutcome,
)

type ResolutionOutcomeType = (
    ResolvedOutcome
    | WaitingOutcome
    | RetryLaterOutcome
    | NextStrategyOutcome
    | ConfigFailureOutcome
    | UnknownOutcome
)

__all__ = ["ResolutionOutcomeType"]

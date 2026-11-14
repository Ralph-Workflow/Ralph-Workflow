from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    RetryLaterOutcome,
)

if TYPE_CHECKING:
    from ralph.pipeline.resolution_outcome_type import ResolutionOutcomeType

__all__ = [
    "RETRY_LATER_REASON",
    "classify_resolution_failure",
    "config_failure_from_exception",
]

RETRY_LATER_REASON = "conflict resolution incomplete; progress retained for next agent"


def config_failure_from_exception(exc: BaseException) -> ConfigFailureOutcome | None:
    from ralph.config._toml_fallback import config_parse_error_of

    payload = config_parse_error_of(exc)
    if payload is None:
        return None
    return ConfigFailureOutcome(file=payload.file, parse_error=str(payload.parse_error))


def classify_resolution_failure(
    *,
    terminal_reason: ResolutionTerminationReason | None,
    failure_cause: ResolutionFailureCause,
    reason: str | None = None,
    paths: tuple[str, ...] = (),
    agents: tuple[str, ...] = (),
    config_error: BaseException | None = None,
) -> ResolutionOutcomeType:
    if config_error is not None:
        typed = config_failure_from_exception(config_error)
        if typed is not None:
            return typed
    if failure_cause is ResolutionFailureCause.CONFIG_FAILURE:
        return RetryLaterOutcome(on_change=reason or RETRY_LATER_REASON)
    if terminal_reason is ResolutionTerminationReason.OUT_OF_REACH:
        return NextStrategyOutcome(strategy="completion_in_place")
    if terminal_reason is ResolutionTerminationReason.CANDIDATE_EXHAUSTED:
        return RetryLaterOutcome(on_change=reason or RETRY_LATER_REASON)
    return RetryLaterOutcome(on_change=reason or RETRY_LATER_REASON)

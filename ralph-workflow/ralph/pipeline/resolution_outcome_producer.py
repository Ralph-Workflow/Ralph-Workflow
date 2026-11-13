from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.conflict_resolution._operator_command import operator_command_for_paths
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.resolution_outcome_config_failure import ConfigFailure
from ralph.pipeline.resolution_outcome_exhausted import Exhausted
from ralph.pipeline.resolution_outcome_needs_operator import NeedsOperator
from ralph.pipeline.resolution_outcome_retry_later import RetryLater

if TYPE_CHECKING:
    from ralph.pipeline.resolution_outcome_type import ResolutionOutcomeType

__all__ = [
    "RETRY_LATER_REASON",
    "classify_resolution_failure",
    "config_failure_from_exception",
]

RETRY_LATER_REASON = "conflict resolution incomplete; progress retained for next agent"


def config_failure_from_exception(exc: BaseException) -> ConfigFailure | None:
    from ralph.config._toml_fallback import config_parse_error_of

    payload = config_parse_error_of(exc)
    if payload is None:
        return None
    return ConfigFailure.from_payload(payload)


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
        return RetryLater(on_change=reason or RETRY_LATER_REASON)
    if terminal_reason is ResolutionTerminationReason.OUT_OF_REACH:
        return NeedsOperator(
            cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
            paths=paths,
            command=operator_command_for_paths(paths),
        )
    if terminal_reason is ResolutionTerminationReason.CANDIDATE_EXHAUSTED:
        return Exhausted(agents=agents)
    return RetryLater(on_change=reason or RETRY_LATER_REASON)

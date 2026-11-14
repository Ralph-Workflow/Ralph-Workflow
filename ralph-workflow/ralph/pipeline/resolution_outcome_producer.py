from __future__ import annotations

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.resolution_outcome import (
    ConfigFailureOutcome,
    NextStrategyOutcome,
    ResolvedOutcome,
    RetryLaterOutcome,
    StopResolutionOutcome,
    UnknownOutcome,
    WaitingOutcome,
)
from ralph.pipeline.resolution_outcome_config_failure import ConfigFailure
from ralph.pipeline.resolution_outcome_exhausted import Exhausted
from ralph.pipeline.resolution_outcome_needs_operator import NeedsOperator
from ralph.pipeline.resolution_outcome_resolved import Resolved
from ralph.pipeline.resolution_outcome_retry_later import RetryLater

__all__ = [
    "RETRY_LATER_REASON",
    "classify_resolution_failure",
    "config_failure_from_exception",
    "stop_resolution_outcome_from_legacy",
]

RETRY_LATER_REASON = "conflict resolution incomplete; progress retained for next agent"


def config_failure_from_exception(exc: BaseException) -> ConfigFailureOutcome | None:
    from ralph.config._toml_fallback import config_parse_error_of

    payload = config_parse_error_of(exc)
    if payload is None:
        return None
    return ConfigFailureOutcome(file=payload.file, parse_error=payload.parse_error)


def classify_resolution_failure(
    *,
    terminal_reason: ResolutionTerminationReason | None,
    failure_cause: ResolutionFailureCause,
    reason: str | None = None,
    paths: tuple[str, ...] = (),
    agents: tuple[str, ...] = (),
    config_error: BaseException | None = None,
) -> StopResolutionOutcome:
    if config_error is not None:
        typed = config_failure_from_exception(config_error)
        if typed is not None:
            return ConfigFailureOutcome(file=typed.file, parse_error=typed.parse_error)
    if failure_cause is ResolutionFailureCause.CONFIG_FAILURE:
        return RetryLaterOutcome(on_change=reason or RETRY_LATER_REASON)
    if terminal_reason is ResolutionTerminationReason.OUT_OF_REACH:
        return NextStrategyOutcome(strategy="completion_in_place")
    if terminal_reason is ResolutionTerminationReason.CANDIDATE_EXHAUSTED:
        return RetryLaterOutcome(on_change=reason or "resolver_agent_eligibility_changed")
    return RetryLaterOutcome(on_change=reason or RETRY_LATER_REASON)


def stop_resolution_outcome_from_legacy(
    outcome: object,
) -> StopResolutionOutcome:
    if isinstance(
        outcome,
        (
            ResolvedOutcome,
            WaitingOutcome,
            RetryLaterOutcome,
            NextStrategyOutcome,
            ConfigFailureOutcome,
            UnknownOutcome,
        ),
    ):
        return outcome
    result: StopResolutionOutcome
    match outcome:
        case Resolved():
            result = ResolvedOutcome()
        case NeedsOperator(command=command):
            result = WaitingOutcome(
                external_condition=(
                    f"operator action required: {command}"
                    if command
                    else "operator action required"
                ),
                resumes_when="operator resolves the conflict",
            )
        case RetryLater(until=until, on_change=on_change):
            result = RetryLaterOutcome(until=until, on_change=on_change)
        case Exhausted(agents=agents):
            result = NextStrategyOutcome(strategy=f"resolver agents exhausted: {', '.join(agents)}")
        case ConfigFailure(file=file, parse_error=parse_error):
            return ConfigFailureOutcome(file=file, parse_error=parse_error)
        case _:
            result = UnknownOutcome(error=RuntimeError(f"unrecognized legacy outcome: {outcome!r}"))
    return result

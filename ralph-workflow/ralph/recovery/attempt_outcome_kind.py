from __future__ import annotations

from enum import StrEnum
from typing import Final

__all__ = ("AttemptOutcomeKind",)

_REQUIRED_ATTEMPT_OUTCOME_KIND_VALUES: Final[tuple[str, ...]] = (
    "completed",
    "retryable_failure",
    "runtime_ended",
    "task_or_environment_failure",
    "agent_dead",
    "unknown",
)


class _AttemptOutcomeKind(StrEnum):
    COMPLETED = "completed"
    RETRYABLE_FAILURE = "retryable_failure"
    RUNTIME_ENDED = "runtime_ended"
    TASK_OR_ENVIRONMENT_FAILURE = "task_or_environment_failure"
    AGENT_DEAD = "agent_dead"
    UNKNOWN = "unknown"


if tuple(kind.value for kind in _AttemptOutcomeKind) != _REQUIRED_ATTEMPT_OUTCOME_KIND_VALUES:
    raise RuntimeError(
        "AttemptOutcomeKind must expose exactly completed, retryable_failure, runtime_ended, "
        "task_or_environment_failure, agent_dead, unknown"
    )


AttemptOutcomeKind = _AttemptOutcomeKind

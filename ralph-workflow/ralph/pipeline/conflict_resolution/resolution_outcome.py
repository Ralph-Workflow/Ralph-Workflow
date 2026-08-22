"""Typed terminal result for one conflict-resolution invocation."""

from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum


class ResolutionTerminationReason(StrEnum):
    """Reason a resolver returned control to its conflict owner."""

    CONFLICT_INACTIVITY = "CONFLICT_INACTIVITY"
    OPERATOR_CAP_REACHED = "OPERATOR_CAP_REACHED"
    SUPERVISION_INFRASTRUCTURE_FAILURE = "SUPERVISION_INFRASTRUCTURE_FAILURE"
    CANDIDATE_DECLINED = "CANDIDATE_DECLINED"
    EXCEPTION = "EXCEPTION"


@dataclass(frozen=True)
class ResolutionAttemptError(Exception):
    """Typed unsuccessful attempt result that must bypass generic recovery."""

    outcome: ResolutionOutcome

    def __str__(self) -> str:
        return self.outcome.reason.value if self.outcome.reason is not None else "resolution failed"


@dataclass(frozen=True)
class ResolutionOutcome:
    """Observable resolution attempt outcome used for status and terminal handling."""

    succeeded: bool
    reason: ResolutionTerminationReason | None
    duration_seconds: float
    last_activity_kind: str | None
    last_activity_at: float | None
    unresolved_paths: tuple[str, ...]


__all__ = ["ResolutionAttemptError", "ResolutionOutcome", "ResolutionTerminationReason"]

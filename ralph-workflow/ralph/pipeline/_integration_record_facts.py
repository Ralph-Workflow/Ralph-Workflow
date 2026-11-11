
from __future__ import annotations

import time
from typing import TYPE_CHECKING, Final

from ralph.pipeline._integration_action import IntegrationAction, RebaseOutcomeKind
from ralph.pipeline.auto_integrate_backoff import retry_deadline_is_live
from ralph.pipeline.integration_observation import (
    InProgressKind,
    IntegrationRecordState,
    TargetVsFeature,
    WorktreeState,
)
from ralph.pipeline.integration_owner import RecordFactOutcome
from ralph.pipeline.rebase_state import RebaseState

if TYPE_CHECKING:
    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.integration_observation import Observation

__all__ = [
    "RECORD_FACT_ACTIONS",
    "apply_record_facts",
    "busy_refusal_outcome",
    "busy_refusal_state",
    "live_git_supersedes_record",
    "record_requires_intervention",
]


RECORD_FACT_ACTIONS: Final[frozenset[IntegrationAction]] = frozenset(
    {
        IntegrationAction.PUBLISH_AFTER_LANDING,
        IntegrationAction.COMPLETE_WITH_PROOF,
    }
)


def record_requires_intervention(record: IntegrationRecord | None) -> bool:
    if record is None:
        return False
    return record.diagnostic_intervention_required is True


def live_git_supersedes_record(observation: Observation | None) -> bool:
    if observation is None:
        return False
    if observation.record is IntegrationRecordState.FOREIGN:
        return True
    return (
        observation.target_vs_feature in (TargetVsFeature.EQUAL, TargetVsFeature.AHEAD)
        and observation.worktree is WorktreeState.CLEAN
        and observation.in_progress_kind is InProgressKind.NONE
    )


def apply_record_facts(
    action: IntegrationAction,
    record: IntegrationRecord | None,
    *,
    retained: bool = False,
    observation: Observation | None = None,
) -> IntegrationAction | RecordFactOutcome:
    if observation is not None and live_git_supersedes_record(observation):
        return action
    if record_requires_intervention(record):
        return RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT
    if record is None and retained:
        return RecordFactOutcome.STALE_INTENT
    return action


def busy_refusal_state(retry_deadline: float | None) -> RebaseState:
    return RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        retry_deadline=retry_deadline,
    )


def busy_refusal_outcome(
    retry_deadline: float | None,
    *,
    clock: object = None,
) -> IntegrationAction | RecordFactOutcome:
    if not isinstance(retry_deadline, (int, float)) or isinstance(retry_deadline, bool):
        return RecordFactOutcome.STALE_INTENT
    now = _current_time(clock)
    if now is None:
        now = time.monotonic()
    if not retry_deadline_is_live(float(retry_deadline), now):
        return RecordFactOutcome.STALE_INTENT
    return IntegrationAction.WAIT_FOR_BOUNDED_RETRY


def _current_time(clock: object) -> float | None:
    if callable(clock):
        try:
            value: object = clock()
            if isinstance(value, (int, float)):
                return float(value)
            if isinstance(value, str):
                return float(value)
            return None
        except Exception:
            return None
    if isinstance(clock, (int, float)):
        return float(clock)
    return None

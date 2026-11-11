
from __future__ import annotations

from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Final

from ralph.pipeline._integration_action import IntegrationAction
from ralph.pipeline._record_fact_outcome import RecordFactOutcome
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_decision import (
    CLEANUP_LADDER_ORDER,
    AttemptState,
    CleanupLadderRung,
    DecisionContext,
    DirtAttribution,
    NextAction,
    decide,
)
from ralph.pipeline.integration_observation import (
    DirtShape,
    IntegrationRecordState,
    classify_worktree,
)

if TYPE_CHECKING:
    from pathlib import Path

    from ralph.pipeline.auto_integrate_record import IntegrationRecord
    from ralph.pipeline.integration_attempt_ledger import AttemptLedgerState
    from ralph.pipeline.integration_observation import Observation

__all__ = [
    "MUTATION_CLASS_ACTIONS",
    "NEXT_ACTION_TO_INTEGRATION_ACTION",
    "NEXT_ACTION_TO_LADDER_RUNG",
    "IntegrationOwnerDecision",
    "RecordFactOutcome",
    "charge_integration_attempt",
    "decide_integration",
    "decide_integration_verdict",
    "is_chargeable",
    "ladder_rung_for_action",
    "ladder_rung_for_record_fact_outcome",
    "load_integration_attempts",
    "project_integration_action",
]


MUTATION_CLASS_ACTIONS: Final[frozenset[NextAction]] = frozenset(
    {
        NextAction.COMMIT,
        NextAction.LAND,
        NextAction.REINTEGRATE,
        NextAction.RESUME_OWNED_OPERATION,
        NextAction.REPAIR,
        NextAction.DISPATCH_RESOLVER,
        NextAction.REBUILD_ON_TARGET,
        NextAction.COMMIT_VIA_COMMIT_PHASE,
        NextAction.SAVE_AND_RESTORE,
        NextAction.PRESERVE_VIA_TEMP_INDEX,
        NextAction.QUARANTINE_AND_RESTORE,
        NextAction.REBUILD_IN_FRESH_WORKTREE,
        NextAction.ADAPT_ENVIRONMENT_AND_RETRY,
        NextAction.LAST_RESORT_RECOVERY,
    }
)

NEXT_ACTION_TO_INTEGRATION_ACTION: Final[dict[NextAction, IntegrationAction]] = {
    NextAction.COMMIT: IntegrationAction.CONTINUE_GIT_OPERATION,
    NextAction.LAND: IntegrationAction.LAND_TARGET,
    NextAction.REINTEGRATE: IntegrationAction.REINTEGRATE_AGAINST_TARGET,
    NextAction.RESUME_OWNED_OPERATION: IntegrationAction.CONTINUE_GIT_OPERATION,
    NextAction.REPAIR: IntegrationAction.RESOLVE_ACTIVE_OPERATION,
    NextAction.DISPATCH_RESOLVER: IntegrationAction.RESOLVE_ACTIVE_OPERATION,
    NextAction.REBUILD_ON_TARGET: IntegrationAction.REINTEGRATE_AGAINST_TARGET,
    NextAction.CONTINUE_WITH_INTEGRATION_PENDING: IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
    NextAction.WAIT_FOR_ENVIRONMENT: IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
    NextAction.COMMIT_VIA_COMMIT_PHASE: IntegrationAction.CONTINUE_GIT_OPERATION,
    NextAction.SAVE_AND_RESTORE: IntegrationAction.CONTINUE_GIT_OPERATION,
    NextAction.PRESERVE_VIA_TEMP_INDEX: IntegrationAction.CONTINUE_GIT_OPERATION,
    NextAction.QUARANTINE_AND_RESTORE: IntegrationAction.CONTINUE_GIT_OPERATION,
    NextAction.REBUILD_IN_FRESH_WORKTREE: IntegrationAction.REINTEGRATE_AGAINST_TARGET,
    NextAction.ADAPT_ENVIRONMENT_AND_RETRY: IntegrationAction.WAIT_FOR_BOUNDED_RETRY,
    NextAction.LAST_RESORT_RECOVERY: IntegrationAction.CONTINUE_GIT_OPERATION,
}

NEXT_ACTION_TO_LADDER_RUNG: Final[dict[NextAction | RecordFactOutcome, CleanupLadderRung]] = {
    NextAction.SAVE_AND_RESTORE: CleanupLadderRung.NORMAL_CLEANUP,
    NextAction.PRESERVE_VIA_TEMP_INDEX: CleanupLadderRung.ALTERNATE_PRESERVATION,
    NextAction.QUARANTINE_AND_RESTORE: CleanupLadderRung.QUARANTINE,
    NextAction.REBUILD_IN_FRESH_WORKTREE: CleanupLadderRung.FRESH_WORKTREE,
    NextAction.ADAPT_ENVIRONMENT_AND_RETRY: CleanupLadderRung.ENVIRONMENT_DEGRADE,
    NextAction.LAST_RESORT_RECOVERY: CleanupLadderRung.LAST_RESORT,
    RecordFactOutcome.STALE_INTENT: CleanupLadderRung.NORMAL_CLEANUP,
    RecordFactOutcome.DIAGNOSTIC_BUDGET_SPENT: CleanupLadderRung.NORMAL_CLEANUP,
}

if set(NEXT_ACTION_TO_LADDER_RUNG.values()) != set(CLEANUP_LADDER_ORDER):
    raise RuntimeError(
        "NEXT_ACTION_TO_LADDER_RUNG must route exactly the CLEANUP_LADDER_ORDER rungs"
    )

_RECORD_FACT_OUTCOMES_WITHOUT_RUNG = set(RecordFactOutcome) - set(NEXT_ACTION_TO_LADDER_RUNG)
if _RECORD_FACT_OUTCOMES_WITHOUT_RUNG:
    raise RuntimeError(
        "RecordFactOutcome members without ladder-rung routing: "
        + ", ".join(sorted(member.value for member in _RECORD_FACT_OUTCOMES_WITHOUT_RUNG))
    )


@dataclass(frozen=True)
class IntegrationOwnerDecision:

    action: IntegrationAction
    reason: str
    observation: Observation
    attempts: AttemptLedgerState
    escalation_charged: bool
    ladder_rung: CleanupLadderRung | None = None
    dirt_shape: DirtShape = DirtShape.NONE
    dirt_attribution: DirtAttribution = DirtAttribution.UNKNOWN


def project_integration_action(next_action: NextAction) -> IntegrationAction:
    try:
        return NEXT_ACTION_TO_INTEGRATION_ACTION[next_action]
    except KeyError as exc:
        raise ValueError(f"no IntegrationAction mapping for NextAction {next_action!r}") from exc


def is_chargeable(next_action: NextAction) -> bool:
    return next_action in MUTATION_CLASS_ACTIONS


def ladder_rung_for_action(next_action: NextAction) -> CleanupLadderRung | None:
    return NEXT_ACTION_TO_LADDER_RUNG.get(next_action)


def ladder_rung_for_record_fact_outcome(outcome: RecordFactOutcome) -> CleanupLadderRung:
    return NEXT_ACTION_TO_LADDER_RUNG[outcome]


def decide_integration(
    root: Path,
    *,
    record: IntegrationRecord | None = None,
    agent_active: bool = False,
    ledger_path: Path | None = None,
    observation: Observation | None = None,
    unknown_budget: int = 3,
    repeat_threshold: int = 2,
    target: str | None = None,
    remote: str | None = None,
) -> tuple[IntegrationAction, str]:
    verdict = decide_integration_verdict(
        root,
        record=record,
        agent_active=agent_active,
        ledger_path=ledger_path,
        observation=observation,
        unknown_budget=unknown_budget,
        repeat_threshold=repeat_threshold,
        target=target,
        remote=remote,
    )
    return verdict.action, verdict.reason


def decide_integration_verdict(
    root: Path,
    *,
    record: IntegrationRecord | None = None,
    agent_active: bool = False,
    ledger_path: Path | None = None,
    observation: Observation | None = None,
    unknown_budget: int = 3,
    repeat_threshold: int = 2,
    target: str | None = None,
    remote: str | None = None,
    dirt_shape: DirtShape | None = None,
    dirt_attribution: DirtAttribution = DirtAttribution.UNOWNED,
) -> IntegrationOwnerDecision:
    current = (
        observation
        if observation is not None
        else _observe(
            root,
            agent_active=agent_active,
            target=target,
            remote=remote,
        )
    )
    current = _reconcile_record(current, record)
    shape = dirt_shape if dirt_shape is not None else _refine_dirt_shape(current)
    attempts = load_integration_attempts(
        root,
        ledger_path=ledger_path,
        unknown_budget=unknown_budget,
        repeat_threshold=repeat_threshold,
    )
    next_action = decide(
        DecisionContext(
            observation=current,
            attempts=attempts,
            dirt_shape=shape,
            dirt_attribution=dirt_attribution,
        )
    )
    action = project_integration_action(next_action)
    charged = charge_integration_attempt(
        root,
        observation=current,
        next_action=next_action,
        ledger_path=ledger_path,
        unknown_budget=unknown_budget,
        repeat_threshold=repeat_threshold,
    )
    return IntegrationOwnerDecision(
        action=action,
        reason=_reason(current, next_action, action),
        observation=current,
        attempts=charged,
        escalation_charged=is_chargeable(next_action),
        ladder_rung=ladder_rung_for_action(next_action),
        dirt_shape=shape,
        dirt_attribution=dirt_attribution,
    )


def load_integration_attempts(
    root: Path,
    *,
    ledger_path: Path | None = None,
    unknown_budget: int = 3,
    repeat_threshold: int = 2,
) -> AttemptState:
    ledger = AttemptLedger(
        root,
        ledger_path=ledger_path,
        unknown_budget=unknown_budget,
        repeat_threshold=repeat_threshold,
    )
    return _to_attempt_state(ledger.read())


def charge_integration_attempt(
    root: Path,
    *,
    observation: Observation,
    next_action: NextAction,
    ledger_path: Path | None = None,
    unknown_budget: int = 3,
    repeat_threshold: int = 2,
) -> AttemptLedgerState:
    ledger = AttemptLedger(
        root,
        ledger_path=ledger_path,
        unknown_budget=unknown_budget,
        repeat_threshold=repeat_threshold,
    )
    state = ledger.read()
    charged = _charge(ledger, observation, next_action, attempts=_to_attempt_state(state))
    return state if charged is None else charged


def _observe(
    root: Path,
    *,
    agent_active: bool,
    target: str | None,
    remote: str | None,
) -> Observation:
    from ralph.pipeline.integration_director import (
        DEFAULT_REMOTE,
        DEFAULT_TARGET_BRANCH,
        IntegrationDirector,
    )

    return IntegrationDirector(
        root,
        target=DEFAULT_TARGET_BRANCH if target is None else target,
        remote=DEFAULT_REMOTE if remote is None else remote,
        agent_active=agent_active,
    ).observe()


def _reconcile_record(observation: Observation, record: IntegrationRecord | None) -> Observation:
    if record is None or observation.record is not IntegrationRecordState.NONE:
        return observation
    return replace(observation, record=IntegrationRecordState.STALE)


def _refine_dirt_shape(observation: Observation) -> DirtShape:
    return classify_worktree(observation.worktree).dirt_shape


def _to_attempt_state(state: AttemptLedgerState) -> AttemptState:
    return AttemptState(
        rung=state.rung,
        repeat_counts=state.repeat_counts,
        unknown_attempts=state.unknown_attempts,
        unknown_budget=state.unknown_budget,
        repeat_threshold=state.repeat_threshold,
    )


def _charge(
    ledger: AttemptLedger,
    observation: Observation,
    next_action: NextAction,
    *,
    attempts: AttemptState,
) -> AttemptLedgerState | None:
    signature = attempts.signature(observation)
    action_value: str = next_action.value
    if next_action is NextAction.WAIT_FOR_ENVIRONMENT:
        return ledger.charge(signature, action_value, is_unknown=True)
    if not is_chargeable(next_action):
        return None
    return ledger.charge(signature, action_value, is_unknown=False)


def _reason(observation: Observation, next_action: NextAction, action: IntegrationAction) -> str:
    return (
        f"worktree={observation.worktree.value}, "
        f"target_vs_feature={observation.target_vs_feature.value}, "
        f"target_vs_remote={observation.target_vs_remote.value}, "
        f"head={observation.head.value}, "
        f"in_progress={observation.in_progress_kind.value}/"
        f"{observation.in_progress_ownership.value}, "
        f"record={observation.record.value}, "
        f"agent_active={str(observation.agent_active).lower()}: "
        f"{next_action.value} -> {action.value}"
    )


def _assert_projection_is_total() -> None:
    mapped = set(NEXT_ACTION_TO_INTEGRATION_ACTION)
    members = set(NextAction)
    missing = members - mapped
    if missing:
        raise RuntimeError(
            "NextAction members without an IntegrationAction mapping: "
            + ", ".join(sorted(member.value for member in missing))
        )
    unknown = mapped - members
    if unknown:
        raise RuntimeError(
            "IntegrationAction mapping for unknown NextAction members: "
            + ", ".join(sorted(member.value for member in unknown))
        )


_assert_projection_is_total()

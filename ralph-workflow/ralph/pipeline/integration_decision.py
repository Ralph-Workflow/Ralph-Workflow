from __future__ import annotations

from collections import OrderedDict
from dataclasses import dataclass, field
from enum import Enum
from typing import Final

from ralph.pipeline.integration_observation import (
    DirtShape,
    HeadState,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    TargetVsFeature,
    WorktreeState,
)


class NextAction(Enum):
    COMMIT = "commit"
    LAND = "land"
    REINTEGRATE = "reintegrate"
    RESUME_OWNED_OPERATION = "resume_owned_operation"
    REPAIR = "repair"
    DISPATCH_RESOLVER = "dispatch_resolver"
    REBUILD_ON_TARGET = "rebuild_on_target"
    CONTINUE_WITH_INTEGRATION_PENDING = "continue_with_integration_pending"
    WAIT_FOR_ENVIRONMENT = "wait_for_environment"
    COMMIT_VIA_COMMIT_PHASE = "commit_via_commit_phase"
    SAVE_AND_RESTORE = "save_and_restore"
    PRESERVE_VIA_TEMP_INDEX = "preserve_via_temp_index"
    QUARANTINE_AND_RESTORE = "quarantine_and_restore"
    REBUILD_IN_FRESH_WORKTREE = "rebuild_in_fresh_worktree"
    ADAPT_ENVIRONMENT_AND_RETRY = "adapt_environment_and_retry"
    LAST_RESORT_RECOVERY = "last_resort_recovery"


class DirtAttribution(Enum):
    PHASE_OWNED = "phase_owned"
    UNOWNED = "unowned"
    UNKNOWN = "unknown"


class CleanupLadderRung(Enum):
    NORMAL_CLEANUP = "normal_cleanup"
    ALTERNATE_PRESERVATION = "alternate_preservation"
    QUARANTINE = "quarantine"
    FRESH_WORKTREE = "fresh_worktree"
    ENVIRONMENT_DEGRADE = "environment_degrade"
    LAST_RESORT = "last_resort"


CLEANUP_LADDER_ORDER: tuple[CleanupLadderRung, ...] = (
    CleanupLadderRung.NORMAL_CLEANUP,
    CleanupLadderRung.ALTERNATE_PRESERVATION,
    CleanupLadderRung.QUARANTINE,
    CleanupLadderRung.FRESH_WORKTREE,
    CleanupLadderRung.ENVIRONMENT_DEGRADE,
    CleanupLadderRung.LAST_RESORT,
)


class EscalationRung(Enum):
    REPAIR = "repair"
    DISPATCH_RESOLVER = "dispatch_resolver"
    REBUILD_ON_TARGET = "rebuild_on_target"
    CONTINUE_WITH_INTEGRATION_PENDING = "continue_with_integration_pending"


LADDER_ORDER: tuple[EscalationRung, ...] = (
    EscalationRung.REPAIR,
    EscalationRung.DISPATCH_RESOLVER,
    EscalationRung.REBUILD_ON_TARGET,
    EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING,
)

MIN_SIGNATURE_ATTEMPTS_FOR_ESCALATION: Final[int] = 2

ESCALATED_RUNG_STRIDE: Final[int] = 2


type RepeatCounts = OrderedDict[tuple[str, str], int]

_REPEAT_COUNTER_CAP = 1024


@dataclass(frozen=True, slots=True)
class AttemptState:
    rung: EscalationRung = EscalationRung.REPAIR
    repeat_counts: RepeatCounts = field(
        default_factory=OrderedDict,
    )
    unknown_attempts: int = 0
    unknown_budget: int = 3
    repeat_threshold: int = 2

    def __post_init__(self) -> None:
        if self.unknown_attempts < 0:
            raise ValueError("unknown_attempts must be >= 0")
        if self.unknown_budget < 1:
            raise ValueError("unknown_budget must be >= 1")
        if self.repeat_threshold < 1:
            raise ValueError("repeat_threshold must be >= 1")

    def signature(self, observation: Observation) -> str:
        return "|".join(
            (
                observation.target_vs_feature.value,
                observation.worktree.value,
                observation.in_progress_kind.value,
                observation.record.value,
                observation.head.value,
            )
        )

    def key_pair(self, observation: Observation, action: NextAction) -> tuple[str, str]:
        return (self.signature(observation), action.value)

    def next_rung(self) -> EscalationRung:
        idx = LADDER_ORDER.index(self.rung)
        if idx + 1 >= len(LADDER_ORDER):
            return self.rung
        return LADDER_ORDER[idx + 1]

    def unknown_budget_exhausted(self) -> bool:
        return self.unknown_attempts >= self.unknown_budget

    def repeat_count_for(self, observation: Observation, action: NextAction) -> int:
        return self.repeat_counts.get(self.key_pair(observation, action), 0)

    def signature_attempts(self, observation: Observation) -> int:
        signature = self.signature(observation)
        return sum(
            count
            for (key_signature, _action), count in self.repeat_counts.items()
            if key_signature == signature
        )

    def repeat_pressure(self, *, repeat_count: int, signature_attempts: int) -> bool:
        return (
            repeat_count >= self.repeat_threshold
            and signature_attempts >= MIN_SIGNATURE_ATTEMPTS_FOR_ESCALATION
        )

    def should_advance(self, *, repeat_count: int, signature_attempts: int) -> bool:
        if LADDER_ORDER.index(self.rung) >= len(LADDER_ORDER) - 1:
            return False
        return self.repeat_pressure(
            repeat_count=repeat_count, signature_attempts=signature_attempts
        )

    def _with_updated_repeat_counts(self, updated: RepeatCounts) -> AttemptState:
        return AttemptState(
            rung=self.rung,
            repeat_counts=updated,
            unknown_attempts=self.unknown_attempts,
            unknown_budget=self.unknown_budget,
            repeat_threshold=self.repeat_threshold,
        )

    def with_attempt(
        self,
        observation: Observation,
        action: NextAction,
        *,
        is_unknown: bool,
    ) -> AttemptState:
        if is_unknown:
            return AttemptState(
                rung=self.rung,
                repeat_counts=OrderedDict(self.repeat_counts),
                unknown_attempts=self.unknown_attempts + 1,
                unknown_budget=self.unknown_budget,
                repeat_threshold=self.repeat_threshold,
            )
        key = self.key_pair(observation, action)
        updated: RepeatCounts = OrderedDict(self.repeat_counts)
        new_count = updated.get(key, 0) + 1
        updated[key] = new_count
        while len(updated) > _REPEAT_COUNTER_CAP:
            updated.popitem(last=False)
        attempts_after = self.signature_attempts(observation) + 1
        if self.should_advance(repeat_count=new_count, signature_attempts=attempts_after):
            return AttemptState(
                rung=self.next_rung(),
                repeat_counts=updated,
                unknown_attempts=0,
                unknown_budget=self.unknown_budget,
                repeat_threshold=self.repeat_threshold,
            )
        return self._with_updated_repeat_counts(updated)


def cleanup_action_for(rung: CleanupLadderRung) -> NextAction:
    return {
        CleanupLadderRung.NORMAL_CLEANUP: NextAction.SAVE_AND_RESTORE,
        CleanupLadderRung.ALTERNATE_PRESERVATION: NextAction.PRESERVE_VIA_TEMP_INDEX,
        CleanupLadderRung.QUARANTINE: NextAction.QUARANTINE_AND_RESTORE,
        CleanupLadderRung.FRESH_WORKTREE: NextAction.REBUILD_IN_FRESH_WORKTREE,
        CleanupLadderRung.ENVIRONMENT_DEGRADE: NextAction.ADAPT_ENVIRONMENT_AND_RETRY,
        CleanupLadderRung.LAST_RESORT: NextAction.LAST_RESORT_RECOVERY,
    }[rung]


@dataclass(frozen=True, slots=True)
class DecisionContext:
    observation: Observation
    attempts: AttemptState = field(default_factory=AttemptState)
    dirt_shape: DirtShape = DirtShape.NONE
    dirt_attribution: DirtAttribution = DirtAttribution.UNKNOWN


def _is_real_conflict(observation: Observation) -> bool:
    return observation.worktree is WorktreeState.UNMERGED or (
        observation.in_progress_kind is not InProgressKind.NONE
        and observation.in_progress_ownership is InProgressOwnership.FOREIGN
    )


_LADDER_ACTION_MAP: dict[EscalationRung, NextAction] = {
    EscalationRung.REPAIR: NextAction.REPAIR,
    EscalationRung.DISPATCH_RESOLVER: NextAction.DISPATCH_RESOLVER,
    EscalationRung.REBUILD_ON_TARGET: NextAction.REBUILD_ON_TARGET,
    EscalationRung.CONTINUE_WITH_INTEGRATION_PENDING: (
        NextAction.CONTINUE_WITH_INTEGRATION_PENDING
    ),
}


def _ladder_action(rung: EscalationRung) -> NextAction:
    return _LADDER_ACTION_MAP[rung]


_REPAIRABLE_RECORD_STATES: frozenset[IntegrationRecordState] = frozenset(
    {
        IntegrationRecordState.STALE,
        IntegrationRecordState.UNREADABLE,
        IntegrationRecordState.OLDER_VERSION,
        IntegrationRecordState.FOREIGN,
    }
)
_TARGET_MOVED_STATES: frozenset[TargetVsFeature] = frozenset(
    {TargetVsFeature.BEHIND, TargetVsFeature.DIVERGED}
)
_LANDED_TARGET_STATES: frozenset[TargetVsFeature] = frozenset(
    {TargetVsFeature.EQUAL, TargetVsFeature.AHEAD}
)
_DIRTY_WORKTREE_STATES: frozenset[WorktreeState] = frozenset(
    {
        WorktreeState.TRACKED_CHANGES,
        WorktreeState.STAGED,
        WorktreeState.UNTRACKED,
    }
)


def _is_target_moved(observation: Observation) -> bool:
    return observation.target_vs_feature in _TARGET_MOVED_STATES


def _is_landed(observation: Observation) -> bool:
    return observation.worktree is WorktreeState.CLEAN and (
        observation.target_vs_feature in _LANDED_TARGET_STATES
    )


def _is_dirty_without_conflict(observation: Observation) -> bool:
    return observation.worktree in _DIRTY_WORKTREE_STATES


def _is_repairable_record(observation: Observation) -> bool:
    return observation.record in _REPAIRABLE_RECORD_STATES


def _is_owned_in_progress(observation: Observation) -> bool:
    return (
        observation.in_progress_kind is not InProgressKind.NONE
        and observation.in_progress_ownership is InProgressOwnership.OWNED
    )


def _needs_ladder(observation: Observation) -> bool:
    return observation.head is HeadState.DETACHED or _is_repairable_record(observation)


def _ladder_action_for(observation: Observation, attempts: AttemptState) -> NextAction:
    if _is_landed(observation):
        return NextAction.LAND
    if _needs_ladder(observation) or not _is_target_moved(observation):
        return _ladder_action_skipping_resolver(attempts.rung, observation)
    return NextAction.REINTEGRATE


def _is_boring_observation(observation: Observation) -> bool:
    return not (
        _is_real_conflict(observation)
        or observation.head is HeadState.DETACHED
        or _is_repairable_record(observation)
    )


def _ladder_action_skipping_resolver(rung: EscalationRung, observation: Observation) -> NextAction:
    action = _ladder_action(rung)
    if action is NextAction.DISPATCH_RESOLVER and _is_boring_observation(observation):
        return NextAction.REBUILD_ON_TARGET
    return action


_ESCALATED_RUNG_MAP: dict[EscalationRung, EscalationRung] = {
    rung: LADDER_ORDER[min(LADDER_ORDER.index(rung) + ESCALATED_RUNG_STRIDE, len(LADDER_ORDER) - 1)]
    for rung in EscalationRung
}


def _escalated_rung(rung: EscalationRung) -> EscalationRung:
    return _ESCALATED_RUNG_MAP[rung]


def _ladder_dispatch_or_direct(
    attempts: AttemptState,
    observation: Observation,
    *,
    direct: NextAction,
) -> NextAction:
    if attempts.repeat_counts and attempts.repeat_pressure(
        repeat_count=attempts.repeat_count_for(observation, direct),
        signature_attempts=attempts.signature_attempts(observation) + 1,
    ):
        return _ladder_action_skipping_resolver(_escalated_rung(attempts.rung), observation)
    return direct


def _wait_or_continue(attempts: AttemptState) -> NextAction:
    if attempts.unknown_budget_exhausted():
        return NextAction.CONTINUE_WITH_INTEGRATION_PENDING
    return NextAction.WAIT_FOR_ENVIRONMENT


_NON_BLOCKING_SHAPES: frozenset[DirtShape] = frozenset(
    {
        DirtShape.LINE_ENDING_ONLY,
    }
)

_BLOCKING_SHAPES: frozenset[DirtShape] = frozenset(
    {
        DirtShape.UNSTAGED_MODIFICATION,
        DirtShape.STAGED_ONLY,
        DirtShape.STAGED_AND_UNSTAGED,
        DirtShape.MODE_ONLY,
        DirtShape.INTENT_TO_ADD,
        DirtShape.TYPE_CHANGE,
        DirtShape.DELETED,
        DirtShape.RENAMED_OR_COPIED,
        DirtShape.UNTRACKED_BLOCKING,
        DirtShape.INDEX_FLAG_HIDDEN,
    }
)


def _cleanup_escalation_action(
    attempts: AttemptState,
    observation: Observation,
    *,
    direct: NextAction,
) -> NextAction | None:
    if not attempts.repeat_counts or not attempts.repeat_pressure(
        repeat_count=attempts.repeat_count_for(observation, direct),
        signature_attempts=attempts.signature_attempts(observation) + 1,
    ):
        return None
    climbed = attempts.signature_attempts(observation) - attempts.repeat_threshold
    index = max(0, min(climbed, len(CLEANUP_LADDER_ORDER) - 2))
    return cleanup_action_for(CLEANUP_LADDER_ORDER[index + 1])


def _decide_dirty_tree(context: DecisionContext) -> NextAction:
    observation = context.observation
    attempts = context.attempts
    shape = context.dirt_shape

    if shape in _NON_BLOCKING_SHAPES:
        return _ladder_action_for(observation, attempts)
    if shape not in _BLOCKING_SHAPES:
        return _ladder_dispatch_or_direct(
            attempts,
            observation,
            direct=NextAction.COMMIT,
        )
    direct = (
        NextAction.COMMIT_VIA_COMMIT_PHASE
        if context.dirt_attribution is DirtAttribution.PHASE_OWNED
        else NextAction.SAVE_AND_RESTORE
    )
    escalated = _cleanup_escalation_action(attempts, observation, direct=direct)
    if escalated is not None:
        return escalated
    return direct


def decide(context: DecisionContext) -> NextAction:
    observation = context.observation
    attempts = context.attempts

    if observation.has_unknown():
        return _wait_or_continue(attempts)

    if _is_repairable_record(observation) and not _is_landed(observation):
        return _ladder_action_skipping_resolver(attempts.rung, observation)

    if _is_real_conflict(observation):
        return _ladder_dispatch_or_direct(
            attempts,
            observation,
            direct=NextAction.DISPATCH_RESOLVER,
        )

    if _is_owned_in_progress(observation):
        return _ladder_dispatch_or_direct(
            attempts,
            observation,
            direct=NextAction.RESUME_OWNED_OPERATION,
        )

    if _is_dirty_without_conflict(observation):
        return _decide_dirty_tree(context)

    return _ladder_action_for(observation, attempts)


__all__ = [
    "CLEANUP_LADDER_ORDER",
    "ESCALATED_RUNG_STRIDE",
    "LADDER_ORDER",
    "MIN_SIGNATURE_ATTEMPTS_FOR_ESCALATION",
    "AttemptState",
    "CleanupLadderRung",
    "DecisionContext",
    "DirtAttribution",
    "EscalationRung",
    "NextAction",
    "RepeatCounts",
    "cleanup_action_for",
    "decide",
]

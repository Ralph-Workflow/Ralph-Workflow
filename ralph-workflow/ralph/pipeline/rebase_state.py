from __future__ import annotations

from enum import StrEnum
from typing import TYPE_CHECKING

from loguru import logger
from pydantic import ConfigDict, Field, model_validator

from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline._publication_state_machine import PublicationDecision
from ralph.pydantic_compat import RalphBaseModel

if TYPE_CHECKING:
    from typing import Self

    from ralph.display.auto_integrate_message import OperationDisplayState
    from ralph.pipeline._fan_out_authority import FanOutDecision

_FROZEN = ConfigDict(frozen=True)
HISTORY_AWARE_CONFLICT_STRATEGY_INDEX = 3
_LEGACY_AGENT_FAILURE_REASON = "resolution_agent_failure"


class _StartupRetentionReason(StrEnum):
    FAILED_ABORT = "FAILED_ABORT"
    FAILED_RESET = "FAILED_RESET"
    TARGET_POINTER_UNREFRESHABLE = "TARGET_POINTER_UNREFRESHABLE"
    TRANSIENT_FAST_FORWARD_FAILURE = "TRANSIENT_FAST_FORWARD_FAILURE"
    OWNED_TARGET_PRECONDITION_FAILED = "OWNED_TARGET_PRECONDITION_FAILED"
    OWNED_OPERATION_IDENTITY_MISMATCH = "OWNED_OPERATION_IDENTITY_MISMATCH"
    REMOTE_RECONCILE_RETAINED = "REMOTE_RECONCILE_RETAINED"
    OBSERVED_RETAINED_ON_DISK = "OBSERVED_RETAINED_ON_DISK"


_StartupRetentionReason.__name__ = "StartupRetentionReason"
_StartupRetentionReason.__qualname__ = "StartupRetentionReason"
StartupRetentionReason = _StartupRetentionReason


class RebaseState(RalphBaseModel):
    model_config = _FROZEN

    pending: bool = False
    in_progress: bool = False
    completed: bool = False

    last_outcome: RebaseOutcomeKind | None = None
    last_reason: str | None = None
    last_failure_cause: ResolutionFailureCause = ResolutionFailureCause.NONE
    last_target: str | None = None
    fast_forwarded: bool = False

    @property
    def last_action(self) -> str | None:
        return self.last_outcome.value if self.last_outcome is not None else None

    @model_validator(mode="before")
    @classmethod
    def _coerce_legacy_action(cls, data: object) -> object:
        if isinstance(data, dict):
            migrated: dict[str, object] = {str(k): v for k, v in data.items()}
            if "last_failure_cause" not in migrated or migrated.get("last_failure_cause") is None:
                if migrated.get("last_reason") == _LEGACY_AGENT_FAILURE_REASON:
                    migrated["last_failure_cause"] = ResolutionFailureCause.AGENT_FAILURE
                else:
                    migrated["last_failure_cause"] = ResolutionFailureCause.NONE
            has_action = "last_action" in migrated
            raw_action = migrated.get("last_action")
            raw_outcome = migrated.get("last_outcome")

            def _to_kind(val: object) -> RebaseOutcomeKind | None:
                if val is None:
                    return None
                if isinstance(val, RebaseOutcomeKind):
                    return val
                return RebaseOutcomeKind.from_legacy(str(val))

            if has_action and raw_outcome is None:
                if raw_action is None:
                    migrated["last_outcome"] = None
                else:
                    migrated["last_outcome"] = _to_kind(raw_action)
                mapped_outcome: object = migrated["last_outcome"]
                mapped_value: object = (
                    mapped_outcome.value if isinstance(mapped_outcome, RebaseOutcomeKind) else None
                )
                logger.warning(
                    "MIGRATED_LEGACY: last_action {} -> last_outcome {}",
                    raw_action,
                    mapped_value,
                )
                return migrated

            if has_action and raw_action is not None and raw_outcome is not None:
                action_kind = _to_kind(raw_action)
                outcome_kind = _to_kind(raw_outcome)
                if (
                    None in (action_kind, outcome_kind)
                    or RebaseOutcomeKind.UNKNOWN in (action_kind, outcome_kind)
                    or action_kind != outcome_kind
                ):
                    migrated["last_outcome"] = RebaseOutcomeKind.UNKNOWN
                    logger.warning(
                        "MIGRATED_LEGACY: contradictory last_action={} last_outcome={}; set to UNKNOWN",
                        raw_action,
                        raw_outcome,
                    )
                else:
                    migrated["last_outcome"] = outcome_kind
                    outcome_value: object = (
                        outcome_kind.value if isinstance(outcome_kind, RebaseOutcomeKind) else None
                    )
                    logger.warning(
                        "MIGRATED_LEGACY: last_action {} -> last_outcome {}",
                        raw_action,
                        outcome_value,
                    )
                return migrated

            if raw_outcome is not None and isinstance(raw_outcome, str):
                outcome_kind = _to_kind(raw_outcome)
                if outcome_kind == RebaseOutcomeKind.UNKNOWN:
                    migrated["last_outcome"] = RebaseOutcomeKind.UNKNOWN
                    logger.warning(
                        "MIGRATED_LEGACY: unknown last_outcome {} set to UNKNOWN",
                        raw_outcome,
                    )
                    return migrated

            return migrated
        return data

    @classmethod
    def from_legacy_payload(cls, payload: dict[str, object]) -> Self:
        validated = cls.model_validate(payload)
        legacy_action = payload.get("last_action")
        mapped_outcome = validated.last_outcome
        mapped_val = mapped_outcome.value if mapped_outcome is not None else None
        logger.warning(
            "MIGRATED_LEGACY: last_action {} -> last_outcome {}",
            legacy_action,
            mapped_val,
        )
        return validated

    last_refresh: str | None = None

    freshness_verdict: str | None = None
    freshness_source: str | None = None
    freshness_safe: bool = True
    freshness_target_sha: str | None = None

    reclaimed_worktree_path: str | None = None
    reclaim_snapshot_ref: str | None = None
    reclaim_discarded_path_count: int = 0

    last_push: str | None = None

    last_remote_sync: str | None = None

    last_remote: str | None = None

    last_push_status: str | None = None

    consecutive_conflicts: int = 0
    integration_retry_attempt: int = Field(
        default=0,
        ge=0,
        description="Checkpointed integration backoff attempt; stops increasing at the delay cap",
    )

    retry_deadline: float | None = None

    last_conflict_feature_sha: str | None = None
    last_conflict_target_sha: str | None = None
    last_conflict_paths: tuple[str, ...] = ()
    last_conflict_stage_oids: tuple[str, ...] = ()
    last_conflict_scope: str = ""

    last_conflict_strategy_index: int = 0

    resolving_rebase: bool = False
    resolving_merge: bool = False
    diagnostic_ownership: str | None = None

    recovery_record_retained: bool = False

    recovery_record_retained_reason: StartupRetentionReason | None = None

    resolution_exhausted: bool = False
    resolution_exhaustion_reason: str | None = None

    conflict_strategy_index: int = 0
    conflict_strategies_tried: tuple[str, ...] = ()

    unresolved_integration_carried: bool = False

    last_planning_obstruction: str | None = None

    last_decision: str | None = None

    last_publication_decision: str | None = None

    last_fan_out_decision: str | None = None

    operation_display_state: str | None = None

    @model_validator(mode="after")
    def _populate_operation_display_state(self) -> Self:
        if (
            self.last_decision is None
            and (
                self.last_outcome is RebaseOutcomeKind.LADDER_ESCALATION
                or self.integration_unresolved
                or self.recovery_record_retained
            )
        ):
            object.__setattr__(self, "last_decision", "block_for_recovery")
        if self.operation_display_state is None:
            state = _resolve_operation_display_state(self)
            if state is not None:
                object.__setattr__(self, "operation_display_state", state)
        return self

    legacy_checkpoint_blocked: bool = False
    legacy_phase: str | None = None
    legacy_conflicted_files: tuple[str, ...] = ()
    legacy_resolved_files: tuple[str, ...] = ()
    legacy_error_count: int = 0
    legacy_timestamp: str | None = None
    legacy_source: str | None = None

    @property
    def integration_unresolved(self) -> bool:
        return (
            self.last_outcome
            in (
                RebaseOutcomeKind.CONFLICT,
                RebaseOutcomeKind.UNKNOWN,
                RebaseOutcomeKind.LADDER_ESCALATION,
                RebaseOutcomeKind.INTERVENTION_REQUIRED,
                RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
            )
            or self.recovery_record_retained
            or self.legacy_checkpoint_blocked
            or self.unresolved_integration_carried
            or self.resolving_rebase
            or self.resolving_merge
            or bool(self.diagnostic_ownership)
        )

    def set_recovery_record_retained(self, reason: StartupRetentionReason) -> Self:
        return self.model_copy(
            update={
                "recovery_record_retained": True,
                "recovery_record_retained_reason": reason,
            }
        )

    def set_publication_decision(self, value: PublicationDecision) -> Self:
        if not isinstance(value, PublicationDecision):
            raise ValueError(f"invalid publication decision: {value!r}")
        return self.model_copy(update={"last_publication_decision": value.value})

    def set_fan_out_decision(self, value: FanOutDecision) -> Self:
        from ralph.pipeline._fan_out_authority import FanOutDecision

        if not isinstance(value, FanOutDecision):
            raise ValueError(f"invalid fan_out_decision: {value!r}")
        return self.model_copy(update={"last_fan_out_decision": value.value})

    def set_operation_display_state(self, value: OperationDisplayState) -> Self:
        from ralph.display.auto_integrate_message import OperationDisplayState

        if not isinstance(value, OperationDisplayState):
            raise ValueError(f"invalid operation_display_state: {value!r}")
        return self.model_copy(update={"operation_display_state": value.value})

    def clear_recovery_record_retained(self) -> Self:
        return self.model_copy(
            update={
                "recovery_record_retained": False,
                "recovery_record_retained_reason": None,
            }
        )

    def set_retry_deadline(self, value: float | None) -> Self:
        if value is not None:
            if isinstance(value, bool) or not isinstance(value, (int, float)):
                raise ValueError(f"invalid retry_deadline: {value!r}")
            import math

            if not math.isfinite(float(value)):
                raise ValueError(f"non-finite retry_deadline: {value!r}")
        return self.model_copy(update={"retry_deadline": value})

    def record_consecutive_conflicts(self, value: int | None, cap: int | None = None) -> Self:
        from ralph.pipeline.auto_integrate_conflict_budget import (
            MAX_TRACKED_CONFLICT_ATTEMPTS,
        )

        effective_cap = MAX_TRACKED_CONFLICT_ATTEMPTS if cap is None else max(1, int(cap))
        coerced = 0 if value is None else int(value)
        clamped = max(0, min(coerced, effective_cap))
        return self.model_copy(update={"consecutive_conflicts": clamped})

    def advance_conflict_strategy(self, reason: str) -> Self:
        from ralph.pipeline.auto_integrate_resolution_state import (
            CONFLICT_RESOLUTION_STRATEGIES,
        )

        n = len(CONFLICT_RESOLUTION_STRATEGIES)
        index = self.conflict_strategy_index
        tried = self.conflict_strategies_tried
        if index < n:
            tried = (*tried, f"{CONFLICT_RESOLUTION_STRATEGIES[index]}: {reason}")
            index += 1
        exhausted = index >= n
        return self.model_copy(
            update={
                "conflict_strategy_index": index,
                "conflict_strategies_tried": tried,
                "resolution_exhausted": exhausted,
                "resolution_exhaustion_reason": "; ".join(tried) if exhausted else None,
            }
        )

    def project_conflict_strategy_from(self, source: RebaseState) -> Self:
        return self.model_copy(
            update={
                "conflict_strategy_index": source.conflict_strategy_index,
                "conflict_strategies_tried": source.conflict_strategies_tried,
                "resolution_exhausted": source.resolution_exhausted,
                "resolution_exhaustion_reason": source.resolution_exhaustion_reason,
            }
        )

    def clear_conflict_strategy(self) -> Self:
        return self.model_copy(
            update={
                "conflict_strategy_index": 0,
                "conflict_strategies_tried": (),
                "resolution_exhausted": False,
                "resolution_exhaustion_reason": None,
            }
        )

    def clear_resolution_exhausted(self) -> Self:
        return self.model_copy(
            update={
                "resolution_exhausted": False,
                "resolution_exhaustion_reason": None,
            }
        )

    def set_ladder_escalation(self, reason: str) -> Self:
        return self.model_copy(
            update={"last_outcome": RebaseOutcomeKind.LADDER_ESCALATION, "last_reason": reason}
        )

    def is_terminal_action(self) -> bool:
        return self.last_outcome is not None and self.last_outcome.is_terminal()

    def is_diagnostic_action(self) -> bool:
        return self.last_outcome in (
            RebaseOutcomeKind.DIAGNOSING,
            RebaseOutcomeKind.EVIDENCE_DIAGNOSIS,
            RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
        )

    def is_rebasing_or_merging(self) -> bool:
        return self.last_outcome in (RebaseOutcomeKind.REBASED, RebaseOutcomeKind.MERGED)

    def is_conflict_resolution(self) -> bool:
        return _is_conflict_resolution(self)

    def is_landing(self) -> bool:
        return _is_landing(self)

    def is_skipped(self) -> bool:
        return self.last_outcome == RebaseOutcomeKind.SKIPPED

    @property
    def is_intervention_required(self) -> bool:
        return self.last_outcome in (
            RebaseOutcomeKind.INTERVENTION_REQUIRED,
            RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED,
            RebaseOutcomeKind.LADDER_ESCALATION,
            RebaseOutcomeKind.UNKNOWN,
        )


_DECISION_DISPLAY_MAP: dict[str, str] = {
    "report_terminal_success": "terminal",
    "report_terminal_failure": "terminal",
    "report_intervention_required": "intervention_required",
}

_OUTCOME_DISPLAY_MAP: dict[RebaseOutcomeKind, str] = {
    RebaseOutcomeKind.INTEGRATING: "integrating",
    RebaseOutcomeKind.WAITING: "waiting",
    RebaseOutcomeKind.DIAGNOSING: "evidence_diagnosis",
    RebaseOutcomeKind.EVIDENCE_DIAGNOSIS: "evidence_diagnosis",
    RebaseOutcomeKind.INTERVENTION_REQUIRED: "intervention_required",
    RebaseOutcomeKind.DIAGNOSTIC_INTERVENTION_REQUIRED: "intervention_required",
    RebaseOutcomeKind.LADDER_ESCALATION: "evidence_diagnosis",
    RebaseOutcomeKind.TERMINAL: "terminal",
    RebaseOutcomeKind.COMPLETED: "terminal",
}

_PUBLISHING_DECISIONS: frozenset[str] = frozenset(
    {
        "publish_now",
        "publishing",
        "publish_deferred_remote",
        "publish_deferred_freshness",
    }
)


def _resolve_operation_display_state(rebase: RebaseState) -> str | None:
    decision_state = _decision_display_state(rebase)
    if decision_state is not None:
        return decision_state
    return _prose_display_state(rebase)


def _decision_display_state(rebase: RebaseState) -> str | None:
    if rebase.last_decision in _DECISION_DISPLAY_MAP:
        return _DECISION_DISPLAY_MAP[rebase.last_decision]
    if _is_evidence_diagnosis(rebase):
        return "evidence_diagnosis"
    if _is_conflict_resolution(rebase):
        return "conflict_resolution"
    if rebase.last_publication_decision == "publication_terminal":
        return "terminal"
    if rebase.last_publication_decision in _PUBLISHING_DECISIONS:
        return "publishing"
    return None


def _prose_display_state(rebase: RebaseState) -> str | None:
    if rebase.is_landing():
        return "landing"
    if rebase.is_terminal_action():
        return "terminal"
    if rebase.last_outcome is not None:
        return _OUTCOME_DISPLAY_MAP.get(rebase.last_outcome)
    return None


def _is_evidence_diagnosis(rebase: RebaseState) -> bool:
    return (
        rebase.last_outcome in (RebaseOutcomeKind.DIAGNOSING, RebaseOutcomeKind.EVIDENCE_DIAGNOSIS)
        or bool(rebase.diagnostic_ownership)
        or rebase.recovery_record_retained
    )


def _is_conflict_resolution(rebase: RebaseState) -> bool:
    return (
        rebase.last_outcome == RebaseOutcomeKind.CONFLICT
        or rebase.resolving_rebase
        or rebase.resolving_merge
    )


def _is_landing(rebase: RebaseState) -> bool:
    return (
        rebase.last_outcome in (RebaseOutcomeKind.REBASED, RebaseOutcomeKind.MERGED)
        and not rebase.fast_forwarded
    ) or (
        rebase.last_outcome
        in (
            RebaseOutcomeKind.REBASED,
            RebaseOutcomeKind.MERGED,
            RebaseOutcomeKind.FAST_FORWARDED,
            RebaseOutcomeKind.SKIPPED,
            RebaseOutcomeKind.REMOTE_PUSHED,
        )
        and rebase.fast_forwarded
    )

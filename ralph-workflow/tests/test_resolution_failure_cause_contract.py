"""Contract tests for ResolutionFailureCause enum and its propagation across the pipeline."""

from __future__ import annotations

import json

from ralph.git.merge import MergeResult
from ralph.git.resolution_failure_cause import ResolutionFailureCause
from ralph.pipeline._integration_action import RebaseOutcomeKind
from ralph.pipeline.auto_integrate_budget_seam import (
    apply_completed_attempt_budget,
)
from ralph.pipeline.auto_integrate_conflict_budget import ConflictIdentity
from ralph.pipeline.auto_integrate_outcome import (
    record_conflict,
    record_rebase_outcome,
    record_resolution_exhausted,
    record_skip,
)
from ralph.pipeline.auto_integrate_resolve import (
    RESOLUTION_AGENT_FAILURE,
    RESOLUTION_FAILED,
)
from ralph.pipeline.conflict_resolution._resolution_termination_reason import (
    ResolutionTerminationReason,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.rebase_state import RebaseState


def test_resolution_failure_cause_enum_members() -> None:
    """Enum must contain at least NONE, AGENT_FAILURE, UNRESOLVED_CONFLICT, CONTEXT_FAILURE."""
    assert ResolutionFailureCause.NONE.value == "NONE"
    assert ResolutionFailureCause.AGENT_FAILURE.value == "AGENT_FAILURE"
    assert ResolutionFailureCause.UNRESOLVED_CONFLICT.value == "UNRESOLVED_CONFLICT"
    assert ResolutionFailureCause.CONTEXT_FAILURE.value == "CONTEXT_FAILURE"

    assert ResolutionFailureCause.NONE.name == "NONE"
    assert ResolutionFailureCause.AGENT_FAILURE.name == "AGENT_FAILURE"
    assert ResolutionFailureCause.UNRESOLVED_CONFLICT.name == "UNRESOLVED_CONFLICT"
    assert ResolutionFailureCause.CONTEXT_FAILURE.name == "CONTEXT_FAILURE"


def test_resolution_outcome_dto_defaults_and_override() -> None:
    """ResolutionOutcome carries failure_cause defaulting to NONE."""
    outcome_default = ResolutionOutcome(
        succeeded=True,
        reason=None,
        duration_seconds=1.5,
        last_activity_kind=None,
        last_activity_at=None,
        unresolved_paths=(),
    )
    assert outcome_default.failure_cause == ResolutionFailureCause.NONE

    outcome_failed = ResolutionOutcome(
        succeeded=False,
        reason=ResolutionTerminationReason.ATTEMPT_FAILED,
        duration_seconds=2.0,
        last_activity_kind=None,
        last_activity_at=None,
        unresolved_paths=("file.py",),
        failure_cause=ResolutionFailureCause.AGENT_FAILURE,
    )
    assert outcome_failed.failure_cause == ResolutionFailureCause.AGENT_FAILURE


def test_merge_result_dto_defaults_and_override() -> None:
    """MergeResult carries failure_cause defaulting to NONE."""
    result_default = MergeResult(outcome="success")
    assert result_default.failure_cause == ResolutionFailureCause.NONE

    result_conflict = MergeResult(
        outcome="conflict",
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
    )
    assert result_conflict.failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT


def test_rebase_state_dto_defaults_and_override() -> None:
    """RebaseState carries last_failure_cause defaulting to NONE."""
    state_default = RebaseState()
    assert state_default.last_failure_cause == ResolutionFailureCause.NONE

    state_agent = RebaseState(last_failure_cause=ResolutionFailureCause.AGENT_FAILURE)
    assert state_agent.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE


def test_rebase_state_serialization_round_trip() -> None:
    """RebaseState serializes last_failure_cause by enum name and deserializes it cleanly."""
    state = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason="conflict resolution failed",
        last_failure_cause=ResolutionFailureCause.AGENT_FAILURE,
    )
    dumped = state.model_dump()
    assert dumped["last_failure_cause"] == ResolutionFailureCause.AGENT_FAILURE
    restored = RebaseState.model_validate(dumped)
    assert restored.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE

    json_str = state.model_dump_json()
    data = json.loads(json_str)
    assert data["last_failure_cause"] == "AGENT_FAILURE"
    restored_json = RebaseState.model_validate_json(json_str)
    assert restored_json.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE


def test_rebase_state_legacy_load_missing_field_maps_to_none() -> None:
    """Legacy checkpoints missing last_failure_cause load as NONE."""
    legacy_payload: dict[str, object] = {
        "pending": False,
        "last_outcome": "conflict",
        "last_reason": "endpoint merge conflicted (some conflict)",
    }
    state = RebaseState.model_validate(legacy_payload)
    assert state.last_failure_cause == ResolutionFailureCause.NONE


def test_rebase_state_legacy_load_agent_failure_sentinel_maps_to_agent_failure() -> None:
    """Legacy checkpoint with last_reason equal to RESOLUTION_AGENT_FAILURE maps to AGENT_FAILURE."""
    legacy_payload: dict[str, object] = {
        "pending": False,
        "last_outcome": "conflict",
        "last_reason": RESOLUTION_AGENT_FAILURE,
    }
    state = RebaseState.model_validate(legacy_payload)
    assert state.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE


def test_rebase_state_legacy_load_other_prose_is_not_interpreted() -> None:
    """Prose mentioning sentinel as substring is NOT interpreted as AGENT_FAILURE."""
    legacy_payload: dict[str, object] = {
        "pending": False,
        "last_outcome": "conflict",
        "last_reason": f"prefix {RESOLUTION_AGENT_FAILURE} suffix",
    }
    state = RebaseState.model_validate(legacy_payload)
    assert state.last_failure_cause == ResolutionFailureCause.NONE


def test_rebase_state_explicit_field_overrides_legacy_reason() -> None:
    """An explicit last_failure_cause takes precedence over legacy last_reason."""
    payload: dict[str, object] = {
        "last_failure_cause": "CONTEXT_FAILURE",
        "last_reason": RESOLUTION_AGENT_FAILURE,
    }
    state = RebaseState.model_validate(payload)
    assert state.last_failure_cause == ResolutionFailureCause.CONTEXT_FAILURE


def test_outcome_helpers_record_typed_failure_cause() -> None:
    """Auto-integrate outcome helpers thread failure_cause into RebaseState."""
    skip_state = record_skip(reason="skipped cleanly", target="main")
    assert skip_state.last_failure_cause == ResolutionFailureCause.NONE

    conflict_state = record_conflict(
        reason="agent failed",
        target="main",
        failure_cause=ResolutionFailureCause.AGENT_FAILURE,
    )
    assert conflict_state.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE

    exhausted_state = record_resolution_exhausted(reason="exhausted", target="main")
    assert exhausted_state.last_failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT


def test_record_rebase_outcome_preserves_merge_failure_cause() -> None:
    """record_rebase_outcome pulls failure_cause from merge_outcome on conflict."""
    from ralph.git.rebase.rebase import RebaseConflicts

    merge_res = MergeResult(
        outcome=RESOLUTION_FAILED,
        reason="agent crashed",
        failure_cause=ResolutionFailureCause.AGENT_FAILURE,
    )
    state = record_rebase_outcome(
        rebase_outcome=RebaseConflicts(files=["file.py"]),
        merge_attempted=True,
        merge_outcome=merge_res,
        target="main",
    )
    assert state.last_outcome == RebaseOutcomeKind.CONFLICT
    assert state.last_failure_cause == ResolutionFailureCause.AGENT_FAILURE


def test_apply_completed_attempt_budget_branches_on_enum_only() -> None:
    """Budget seam checks record.last_failure_cause == AGENT_FAILURE, not last_reason."""
    prior = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_target="main",
        last_conflict_feature_sha="1111111111111111111111111111111111111111",
        last_conflict_target_sha="2222222222222222222222222222222222222222",
        consecutive_conflicts=1,
    )
    identity = ConflictIdentity(
        feature_sha="1111111111111111111111111111111111111111",
        target_sha="2222222222222222222222222222222222222222",
    )

    # 1. AGENT_FAILURE: does not charge consecutive_conflicts (preserves prior count)
    agent_record = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason="some operator description without sentinel",
        last_failure_cause=ResolutionFailureCause.AGENT_FAILURE,
    )
    res_agent = apply_completed_attempt_budget(
        agent_record,
        prior=prior,
        target="main",
        identity=identity,
        resolver_suppressed=False,
        attempts=2,
    )
    assert res_agent.consecutive_conflicts == 1

    # 2. UNRESOLVED_CONFLICT: charges consecutive_conflicts (increments count)
    # even if last_reason happened to contain text with 'resolution_agent_failure'
    conflict_record = RebaseState(
        last_outcome=RebaseOutcomeKind.CONFLICT,
        last_reason="arbitrary prose",
        last_failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
    )
    res_conflict = apply_completed_attempt_budget(
        conflict_record,
        prior=prior,
        target="main",
        identity=identity,
        resolver_suppressed=False,
        attempts=2,
    )
    assert res_conflict.consecutive_conflicts == 2

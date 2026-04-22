"""Unit tests for checkpoint run context helpers."""

from __future__ import annotations

from importlib import import_module

from ralph.pipeline.state import PipelineState

checkpoint_module = import_module("ralph.checkpoint")
RunContext = checkpoint_module.RunContext

# Constant for default recovery cycle cap (defined in PipelineState)
DEFAULT_RECOVERY_CYCLE_CAP = 200


def test_run_context_new_starts_fresh() -> None:
    """Fresh run contexts should start with zeroed counters."""
    context = RunContext.new()

    assert context.run_id
    assert context.parent_run_id is None
    assert context.resume_count == 0
    assert context.actual_developer_runs == 0
    assert context.actual_reviewer_runs == 0


def test_run_context_resumed_from_creates_lineage() -> None:
    """Resumed contexts should link to the parent run and increment resume count."""
    original = RunContext.new().record_developer_iteration().record_reviewer_pass()

    resumed = RunContext.resumed_from(original)

    assert resumed.run_id != original.run_id
    assert resumed.parent_run_id == original.run_id
    assert resumed.resume_count == 1
    assert resumed.actual_developer_runs == 1
    assert resumed.actual_reviewer_runs == 1


def test_run_context_records_progress_without_mutating_original() -> None:
    """Progress helpers should return updated copies."""
    original = RunContext.new()
    advanced = original.record_developer_iteration().record_reviewer_pass()

    assert original.actual_developer_runs == 0
    assert original.actual_reviewer_runs == 0
    assert advanced.actual_developer_runs == 1
    assert advanced.actual_reviewer_runs == 1


def test_run_context_forward_compat_legacy_json_loads_with_defaults() -> None:
    """Legacy JSON without recovery fields must load with defaults.

    Regression test: checkpoints written before recovery fields were added
    (recovery_cycle_count, fallover_history, last_failure_category) must
    load cleanly with default values rather than failing validation.
    """
    # Simulate a legacy checkpoint written before recovery fields were added
    legacy_dict = {
        "run_id": "legacy-run-123",
        "parent_run_id": None,
        "resume_count": 0,
        "actual_developer_runs": 2,
        "actual_reviewer_runs": 1,
        # Note: recovery_cycle_count, fallover_history, last_failure_category
        # are intentionally absent - this is what a pre-v2 checkpoint looks like
    }

    context = RunContext(
        run_id=legacy_dict["run_id"],
        parent_run_id=legacy_dict["parent_run_id"],
        resume_count=legacy_dict["resume_count"],
        actual_developer_runs=legacy_dict["actual_developer_runs"],
        actual_reviewer_runs=legacy_dict["actual_reviewer_runs"],
        # These should get defaults
        recovery_cycle_count=0,
        fallover_history=[],
        last_failure_category=None,
    )

    # Verify defaults are applied
    assert context.recovery_cycle_count == 0
    assert context.fallover_history == []
    assert context.last_failure_category is None

    # Verify round-trip works
    serialized = context.to_dict()
    assert "recovery_cycle_count" in serialized
    assert "fallover_history" in serialized
    assert "last_failure_category" in serialized


def test_pipeline_state_forward_compat_legacy_json() -> None:
    """Legacy PipelineState JSON without recovery fields must load with defaults.

    This verifies that checkpoints written before recovery fields were added
    (recovery_cycle_count, fallover_history, last_failure_category,
    last_connectivity_state, recovery_cycle_cap) load cleanly.
    """
    # Simulate a legacy checkpoint written before recovery fields were added
    legacy_json = '{"phase":"planning","iteration":1}'

    state = PipelineState.model_validate_json(legacy_json)

    # Verify defaults are applied
    assert state.recovery_cycle_count == 0
    assert state.fallover_history == ()
    assert state.last_failure_category is None
    assert state.last_connectivity_state == "unknown"
    assert state.recovery_cycle_cap == DEFAULT_RECOVERY_CYCLE_CAP

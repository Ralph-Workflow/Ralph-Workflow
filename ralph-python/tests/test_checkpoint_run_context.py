"""Unit tests for checkpoint run context helpers."""

from __future__ import annotations

from importlib import import_module

checkpoint_module = import_module("ralph.checkpoint")
RunContext = checkpoint_module.RunContext


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

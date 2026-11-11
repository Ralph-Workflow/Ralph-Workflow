"""Tests for auto-integrate merge seam under candidate exhaustion (Defects 8 & 11).

When conflict resolution terminates due to candidate exhaustion:
1. Merge seam must NOT yield the misleading 'conflict resolution incomplete; progress retained' narrative.
2. It must return a MergeResult with failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT.
3. The reason message must name the operator action command and paths.
4. Resolver reasons in RESOLVER_NOT_SPENT_TERMINATION_REASONS still return RESOLUTION_AGENT_FAILURE.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import patch

from ralph.pipeline.auto_integrate_resolve import (
    MERGE_COMMIT_PENDING,
    RESOLUTION_AGENT_FAILURE,
    handle_unsuccessful_resolution,
    resolve_and_commit_with_reason,
)
from ralph.pipeline.conflict_resolution.resolution_outcome import ResolutionOutcome
from ralph.pipeline.conflict_resolution.session import (
    ResolutionFailureCause,
    ResolutionTerminationReason,
)


def test_exhausted_resolution_outcome_yields_operator_action_no_progress_retained() -> None:
    outcome = ResolutionOutcome(
        succeeded=False,
        reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
        duration_seconds=1.0,
        last_activity_kind=None,
        last_activity_at=None,
        unresolved_paths=("conflict_a.py", "conflict_b.py"),
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
    )
    result = handle_unsuccessful_resolution(
        outcome, reason=None, conflicted=("conflict_a.py", "conflict_b.py")
    )
    assert result.outcome == MERGE_COMMIT_PENDING
    assert result.failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT
    assert "progress retained" not in (result.reason or "")
    assert "needs operator action:" in (result.reason or "")
    assert "git add -- conflict_a.py conflict_b.py" in (result.reason or "")
    assert "conflict_a.py, conflict_b.py" in (result.reason or "")


def test_exhausted_reason_string_yields_operator_action() -> None:
    result = handle_unsuccessful_resolution(
        False,
        reason="CANDIDATE_EXHAUSTED: all candidates spent",
        conflicted=("file1.txt",),
    )
    assert result.outcome == MERGE_COMMIT_PENDING
    assert result.failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT
    assert "progress retained" not in (result.reason or "")
    assert "needs operator action:" in (result.reason or "")
    assert "git add -- file1.txt" in (result.reason or "")


def test_tool_surface_dead_still_yields_agent_failure() -> None:
    outcome = ResolutionOutcome(
        succeeded=False,
        reason=ResolutionTerminationReason.TOOL_SURFACE_DEAD,
        duration_seconds=1.0,
        last_activity_kind=None,
        last_activity_at=None,
        unresolved_paths=("file.py",),
        failure_cause=ResolutionFailureCause.AGENT_FAILURE,
    )
    result = handle_unsuccessful_resolution(outcome, reason=None)
    assert result.outcome == MERGE_COMMIT_PENDING
    assert result.failure_cause == ResolutionFailureCause.AGENT_FAILURE
    assert result.reason == RESOLUTION_AGENT_FAILURE


def test_unrelated_unsuccessful_resolution_keeps_progress_retained() -> None:
    result = handle_unsuccessful_resolution(False, reason=None, conflicted=("file.py",))
    assert result.outcome == MERGE_COMMIT_PENDING
    assert result.failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT
    assert result.reason == "conflict resolution incomplete; progress retained"


def test_resolve_conflicts_and_commit_with_exhausted_resolver(tmp_path: Path) -> None:
    outcome = ResolutionOutcome(
        succeeded=False,
        reason=ResolutionTerminationReason.CANDIDATE_EXHAUSTED,
        duration_seconds=1.0,
        last_activity_kind=None,
        last_activity_at=None,
        unresolved_paths=("app.py",),
        failure_cause=ResolutionFailureCause.UNRESOLVED_CONFLICT,
    )

    with (
        patch("ralph.pipeline.auto_integrate_resolve.unmerged_paths", return_value=["app.py"]),
        patch("ralph.pipeline.auto_integrate_resolve.mark_pending_merge"),
    ):
        result = resolve_and_commit_with_reason(
            tmp_path, "main", resolver=lambda _root, _target: outcome
        )
        assert result.outcome == MERGE_COMMIT_PENDING
        assert result.failure_cause == ResolutionFailureCause.UNRESOLVED_CONFLICT
        assert "progress retained" not in (result.reason or "")
        assert "git add -- app.py" in (result.reason or "")

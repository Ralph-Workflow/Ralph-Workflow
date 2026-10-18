"""Tests for shared integration readiness vs development dispatch contract (AC12).

Verifies that integration_resolution, auto_integrate_boundary, and
auto_integrate_planning agree on tree state across the full matrix:
clean, untracked only, modified tracked, staged tracked, unmerged conflicts,
and in-progress rebase/merge.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.merge import MERGE_STATE_NONE
from ralph.pipeline import auto_integrate_boundary as boundary
from ralph.pipeline import auto_integrate_planning as planning
from ralph.pipeline._planning_obstruction import PlanningObstructionKind
from ralph.pipeline.integration_resolution import (
    RECOVERABLE,
    RESOLVED,
    TreeReadinessAssessment,
    assess_tree_readiness,
    inspect_integration_resolution,
)
from ralph.pipeline.rebase_state import RebaseState
from ralph.workspace.scope import WorkspaceScope


def _config() -> UnifiedConfig:
    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
            }
        }
    )


def test_clean_tree_matrix(tmp_path: Path) -> None:
    """State 1: Clean tree allows development dispatch and is integration ready."""
    readiness = assess_tree_readiness(
        tmp_path,
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert readiness.development_dispatch_allowed is True
    assert readiness.integration_ready is True
    assert readiness.blocking_reasons == ()

    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert verdict.status is RESOLVED
    assert verdict.dispatch_allowed is True
    assert verdict.development_dispatch_allowed is True
    assert verdict.integration_ready is True
    assert verdict.blocking_reasons == ()


def test_untracked_only_matrix(tmp_path: Path) -> None:
    """State 2: Untracked files allow development dispatch and do not block integration."""
    porcelain_out = "?? scratch.txt\n?? untracked_dir/\n"
    readiness = assess_tree_readiness(
        tmp_path,
        porcelain=lambda _: (True, porcelain_out),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert readiness.development_dispatch_allowed is True
    assert readiness.integration_ready is True
    assert readiness.blocking_reasons == ()

    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, porcelain_out),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert verdict.status is RESOLVED
    assert verdict.dispatch_allowed is True
    assert verdict.development_dispatch_allowed is True
    assert verdict.integration_ready is True
    assert verdict.blocking_reasons == ()


def test_modified_tracked_matrix(tmp_path: Path) -> None:
    """State 3: Unstaged modified tracked work allows dev dispatch but defers integration."""
    porcelain_out = " M src/foo.py\n"
    readiness = assess_tree_readiness(
        tmp_path,
        porcelain=lambda _: (True, porcelain_out),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert readiness.development_dispatch_allowed is True
    assert readiness.integration_ready is False
    assert any("worktree not clean" in r for r in readiness.blocking_reasons)

    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, porcelain_out),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert verdict.status is RESOLVED
    assert verdict.dispatch_allowed is True
    assert verdict.development_dispatch_allowed is True
    assert verdict.integration_ready is False
    assert any("worktree not clean" in r for r in verdict.blocking_reasons)


def test_staged_tracked_matrix(tmp_path: Path) -> None:
    """State 4: Staged tracked work allows dev dispatch but blocks integration."""
    porcelain_out = "M  src/foo.py\nA  src/bar.py\n"
    readiness = assess_tree_readiness(
        tmp_path,
        porcelain=lambda _: (True, porcelain_out),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert readiness.development_dispatch_allowed is True
    assert readiness.integration_ready is False
    assert any(
        "staged uncommitted work blocks integration" in r for r in readiness.blocking_reasons
    )

    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, porcelain_out),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert verdict.status is RESOLVED
    assert verdict.dispatch_allowed is True
    assert verdict.development_dispatch_allowed is True
    assert verdict.integration_ready is False
    assert any("staged uncommitted work blocks integration" in r for r in verdict.blocking_reasons)


def test_unmerged_conflicts_matrix(tmp_path: Path) -> None:
    """State 5: Unmerged conflicts block BOTH development dispatch and integration."""
    porcelain_out = "UU src/foo.py\n"
    readiness = assess_tree_readiness(
        tmp_path,
        porcelain=lambda _: (True, porcelain_out),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert readiness.development_dispatch_allowed is False
    assert readiness.integration_ready is False
    assert any("unmerged paths remain" in r for r in readiness.blocking_reasons)

    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, porcelain_out),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    assert verdict.status is RECOVERABLE
    assert verdict.dispatch_allowed is False
    assert verdict.development_dispatch_allowed is False
    assert verdict.integration_ready is False
    assert any("unmerged paths remain" in r for r in verdict.blocking_reasons)


@pytest.mark.parametrize(
    ("rebase_active", "merge_status", "expected_reason"),
    [
        (True, MERGE_STATE_NONE, "rebase is in progress"),
        (False, "in_progress", "merge is in progress"),
    ],
)
def test_in_progress_rebase_merge_matrix(
    tmp_path: Path, rebase_active: bool, merge_status: str, expected_reason: str
) -> None:
    """State 6: Active rebase or merge blocks BOTH development dispatch and integration."""
    readiness = assess_tree_readiness(
        tmp_path,
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: rebase_active,
        merge_status=lambda _: merge_status,
    )
    assert readiness.development_dispatch_allowed is False
    assert readiness.integration_ready is False
    assert any(expected_reason in r for r in readiness.blocking_reasons)

    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, ""),
        rebase_active=lambda _: rebase_active,
        merge_status=lambda _: merge_status,
    )
    assert verdict.status is RECOVERABLE
    assert verdict.dispatch_allowed is False
    assert verdict.development_dispatch_allowed is False
    assert verdict.integration_ready is False
    assert any(expected_reason in r for r in verdict.blocking_reasons)


def test_staged_work_does_not_falsely_claim_clean_live_tree(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """If staged work is present, resolution does NOT claim the tree is clean."""
    log_messages: list[str] = []
    monkeypatch.setattr(
        "ralph.pipeline.integration_resolution.logger.info",
        lambda msg, *args: log_messages.append(msg.format(*args) if args else msg),
    )

    stale_conflict = RebaseState(
        last_action="conflict",
        last_reason="staged uncommitted work blocks integration",
        last_planning_obstruction=PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION.value,
    )

    # Tree still has staged work:
    verdict = inspect_integration_resolution(
        tmp_path,
        stale_conflict,
        porcelain=lambda _: (True, "M  src/foo.py\n"),
        rebase_active=lambda _: False,
        merge_status=lambda _: MERGE_STATE_NONE,
    )
    # Development dispatch is allowed (for cleanup/commit):
    assert verdict.development_dispatch_allowed is True
    assert verdict.dispatch_allowed is True
    # But integration is NOT ready:
    assert verdict.integration_ready is False
    # And the logger does NOT claim "now clean" or "clean (..); the live tree decides":
    assert not any("now clean" in m for m in log_messages)
    assert any("uncommitted work" in m for m in log_messages)


def test_planning_staged_block_records_skip_not_unresolved_conflict(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Planning records STAGED_BLOCKS_INTEGRATION as a skip, avoiding conflict loop."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(planning, "resolve_integration_target", lambda _c, _r: "main")
    monkeypatch.setattr(planning, "ancestry_state", lambda _r, _t, _h: False)
    monkeypatch.setattr(
        planning,
        "assess_tree_readiness",
        lambda _root: TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=False,
            blocking_reasons=("staged uncommitted work blocks integration",),
        ),
    )
    monkeypatch.setattr(planning, "_worktree_is_clean", lambda _root: False)
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_recovery_terminal.post_attempt_verify",
        lambda *_a, **_kw: None,
    )

    cfg = _config()
    scope = WorkspaceScope(tmp_path)
    result = planning.integrate_before_planning(cfg, scope, RebaseState())

    assert result is not None
    assert result.last_action == "skipped"
    assert (
        result.last_planning_obstruction == PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION.value
    )
    assert result.last_reason == "staged uncommitted work blocks integration"
    # Since it is a skip, integration_unresolved is False:
    assert result.integration_unresolved is False


def test_boundary_and_planning_agree_on_cleanliness_deferral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Boundary and planning agree when tracked changes are present."""
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(boundary, "_worktree_is_clean", lambda _r: False)
    monkeypatch.setattr(boundary, "branch_sha", lambda _r, _t: "b" * 40)
    monkeypatch.setattr(boundary, "get_head_sha", lambda _r: "a" * 40)
    monkeypatch.setattr(boundary, "is_ancestor", lambda _r, _a, _b: False)

    readiness = TreeReadinessAssessment(
        development_dispatch_allowed=True,
        integration_ready=False,
        blocking_reasons=(
            "worktree not clean; uncommitted tracked changes deferred catch-up integration",
        ),
    )
    monkeypatch.setattr(boundary, "assess_tree_readiness", lambda _root: readiness)

    cfg = _config()
    ended, outcome = boundary.phase_boundary_outcome(
        cfg, tmp_path, "main", RebaseState(), rebase_stop_resolver=None
    )
    assert ended is True
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert "worktree not clean" in str(outcome.last_reason)


@pytest.mark.parametrize(
    (
        "state_name",
        "porcelain_out",
        "rebase_active",
        "merge_status",
        "expected_ready",
        "expected_first_reason",
    ),
    [
        (
            "clean",
            "",
            False,
            MERGE_STATE_NONE,
            True,
            None,
        ),
        (
            "modified-tracked",
            " M src/foo.py\n",
            False,
            MERGE_STATE_NONE,
            False,
            "worktree not clean; uncommitted tracked changes deferred catch-up integration",
        ),
        (
            "staged",
            "M  src/foo.py\n",
            False,
            MERGE_STATE_NONE,
            False,
            "staged uncommitted work blocks integration",
        ),
        (
            "unmerged",
            "UU src/foo.py\n",
            False,
            MERGE_STATE_NONE,
            False,
            "unmerged paths remain from an unfinished rebase or merge: src/foo.py",
        ),
        (
            "rebase-active",
            "",
            True,
            MERGE_STATE_NONE,
            False,
            "rebase is in progress",
        ),
        (
            "merge-active",
            "",
            False,
            "in_progress",
            False,
            "merge is in progress or merge state is unreadable",
        ),
    ],
)
def test_all_gates_agree_on_clean_and_blocked_truth(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    state_name: str,
    porcelain_out: str,
    rebase_active: bool,
    merge_status: str,
    expected_ready: bool,
    expected_first_reason: str | None,
) -> None:
    """Criterion 12: resolution, boundary, and planning agree across all tree states.

    Verifies the one clean/blocked truth contract across clean, modified-tracked,
    staged, unmerged, rebase-active, and merge-active:
    1. Resolution (verdict.integration_ready), boundary (ended / dirty deferral),
       and planning (_record_staged_block / planning sync) all agree on whether
       integration is ready/clean vs blocked.
    2. Resolution and planning agree on the exact first blocking reason, and
       boundary records a cleanliness deferral outcome when blocked.
    """
    (tmp_path / ".git").mkdir(exist_ok=True)
    cfg = _config()
    scope = WorkspaceScope(tmp_path)

    # 1. Integration resolution entry points
    readiness = assess_tree_readiness(
        tmp_path,
        porcelain=lambda _: (True, porcelain_out),
        rebase_active=lambda _: rebase_active,
        merge_status=lambda _: merge_status,
    )
    verdict = inspect_integration_resolution(
        tmp_path,
        RebaseState(),
        porcelain=lambda _: (True, porcelain_out),
        rebase_active=lambda _: rebase_active,
        merge_status=lambda _: merge_status,
    )

    assert readiness.integration_ready is expected_ready
    assert verdict.integration_ready is expected_ready
    if expected_ready:
        assert readiness.blocking_reasons == ()
        assert verdict.blocking_reasons == ()
    else:
        assert len(readiness.blocking_reasons) > 0
        assert readiness.blocking_reasons[0] == expected_first_reason
        assert verdict.blocking_reasons[0] == expected_first_reason

    # 2. Planning entry points (_record_staged_block and integrate_before_planning)
    planning_block = planning._record_staged_block(tmp_path, "main", readiness=readiness)
    if state_name == "staged":
        assert planning_block is not None
        assert planning_block.last_action == "skipped"
        assert planning_block.last_reason == expected_first_reason
        assert (
            planning_block.last_planning_obstruction
            == PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION.value
        )
        assert planning_block.integration_unresolved is False
    else:
        assert planning_block is None

    monkeypatch.setattr(planning, "resolve_integration_target", lambda _c, _r: "main")
    monkeypatch.setattr(planning, "assess_tree_readiness", lambda _root: readiness)
    monkeypatch.setattr(planning, "_worktree_is_clean", lambda _root: expected_ready)
    monkeypatch.setattr(planning, "ancestry_state", lambda _r, _t, _h: False)
    monkeypatch.setattr(
        planning,
        "auto_integrate_after_commit",
        lambda *_a, **_kw: RebaseState(last_outcome="fast_forwarded", fast_forwarded=True),
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_recovery_terminal.post_attempt_verify",
        lambda *_a, **_kw: None,
    )

    if state_name == "staged":
        planning_sync = planning.integrate_before_planning(cfg, scope, RebaseState())
        assert planning_sync is not None
        assert planning_sync.last_action == "skipped"
        assert planning_sync.last_reason == expected_first_reason
        assert (
            planning_sync.last_planning_obstruction
            == PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION.value
        )
    elif expected_ready:
        planning_sync = planning.integrate_before_planning(cfg, scope, RebaseState())
        assert planning_sync is not None
        assert planning_sync.fast_forwarded is True

    # 3. Boundary entry point (phase_boundary_outcome)
    monkeypatch.setattr(boundary, "assess_tree_readiness", lambda _root: readiness)
    monkeypatch.setattr(boundary, "_worktree_is_clean", lambda _root: expected_ready)
    monkeypatch.setattr(boundary, "branch_sha", lambda _r, _t: "b" * 40)
    monkeypatch.setattr(boundary, "get_head_sha", lambda _r: "a" * 40)
    monkeypatch.setattr(boundary, "is_ancestor", lambda _r, _a, _b: False)
    monkeypatch.setattr(boundary, "pull_and_reconcile_target", lambda *_a, **_kw: None)

    boundary_ended, boundary_outcome = boundary.phase_boundary_outcome(
        cfg, tmp_path, "main", RebaseState(), rebase_stop_resolver=None
    )

    if expected_ready:
        # Ready tree proceeds to freshness/sync check (not ended by cleanliness gate)
        assert boundary_ended is False
        assert boundary_outcome is None
    else:
        # Non-ready tree ends at dirty/readiness gate with a recorded cleanliness deferral
        assert boundary_ended is True
        assert boundary_outcome is not None
        assert boundary_outcome.last_action == "skipped"
        assert "worktree not clean" in str(boundary_outcome.last_reason)

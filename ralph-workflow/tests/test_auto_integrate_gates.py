"""Tests for shared integration readiness vs development dispatch contract (AC12).

Verifies that integration_resolution, auto_integrate_boundary, and
auto_integrate_planning agree on tree state across the full matrix:
clean, untracked only, modified tracked, staged tracked, unmerged conflicts,
and in-progress rebase/merge.
"""

from __future__ import annotations

import sys
from collections.abc import Callable
from pathlib import Path
from types import ModuleType

import pytest
from git import Actor, Repo

from ralph.config.models import UnifiedConfig
from ralph.git.merge import MERGE_STATE_NONE
from ralph.pipeline import auto_integrate_boundary as boundary
from ralph.pipeline import auto_integrate_planning as planning
from ralph.pipeline._planning_obstruction import PlanningObstructionKind
from ralph.pipeline.auto_integrate_worktree_state import worktree_is_clean
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


def _assert_resolution_agrees(
    readiness: TreeReadinessAssessment,
    verdict: object,
    expected_ready: bool,
    expected_first_reason: str | None,
) -> None:
    """Criterion 12: integration-resolution gates agree on clean/blocked."""
    assert readiness.integration_ready is expected_ready
    assert verdict.integration_ready is expected_ready
    if expected_ready:
        assert readiness.blocking_reasons == ()
        assert verdict.blocking_reasons == ()
    else:
        assert len(readiness.blocking_reasons) > 0
        assert readiness.blocking_reasons[0] == expected_first_reason
        assert verdict.blocking_reasons[0] == expected_first_reason


def _assert_planning_agrees(
    planning_module: ModuleType,
    tmp_path: Path,
    cfg: UnifiedConfig,
    scope: WorkspaceScope,
    state_name: str,
    expected_ready: bool,
    expected_first_reason: str | None,
    readiness: TreeReadinessAssessment,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Criterion 12: planning gate agrees with the resolution verdict.

    For every non-ready state, planning MUST record a typed
    obstruction that agrees with the boundary / integration-resolution
    verdict. The pre-fix code only did this for staged work and fell
    through silently for every other non-ready state, which is the
    three-gates-disagree symptom the criteria call out.
    """
    # Pass the pre-computed readiness through so the planning seam
    # does not need to read the worktree again (the test already
    # pre-computed the readiness from the parametrized state).
    planning_block = planning_module._record_staged_block(tmp_path, "main", readiness=readiness)
    if expected_ready:
        assert planning_block is None
        return

    assert planning_block is not None
    assert planning_block.last_action == "skipped"
    assert planning_block.last_reason == expected_first_reason
    if state_name == "staged":
        assert (
            planning_block.last_planning_obstruction
            == PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION.value
        )
    else:
        assert planning_block.last_planning_obstruction in (
            PlanningObstructionKind.NONE.value,
            PlanningObstructionKind.LOCAL_OVERWRITE.value,
            PlanningObstructionKind.UNTRACKED_OVERWRITE.value,
        )
    assert planning_block.integration_unresolved is False

    # Stage the planning seam: resolve_integration_target returns
    # "main", assess_tree_readiness is bound to the pre-computed
    # readiness, the post-merge verifier is a no-op, and the auto
    # integrator is a stub so the test is hermetic.
    monkeypatch.setattr(planning_module, "resolve_integration_target", lambda _c, _r: "main")
    monkeypatch.setattr(planning_module, "assess_tree_readiness", lambda _root: readiness)
    monkeypatch.setattr(planning_module, "ancestry_state", lambda _r, _t, _h: False)
    monkeypatch.setattr(
        planning_module,
        "auto_integrate_after_commit",
        lambda *_a, **_kw: RebaseState(last_outcome="fast_forwarded", fast_forwarded=True),
    )

    def _unexpected_force_merge(*_a: object, **_kw: object) -> RebaseState:
        pytest.fail("force merge must not be executed when tree is not integration ready")

    monkeypatch.setattr(
        planning_module,
        "_integrate_with_force_merge",
        _unexpected_force_merge,
    )
    monkeypatch.setattr(
        "ralph.pipeline.auto_integrate_recovery_terminal.post_attempt_verify",
        lambda *_a, **_kw: None,
    )

    planning_sync = planning_module.integrate_before_planning(cfg, scope, RebaseState())
    assert planning_sync is not None
    if state_name == "staged":
        assert planning_sync.last_action == "skipped"
        assert planning_sync.last_reason == expected_first_reason
        assert (
            planning_sync.last_planning_obstruction
            == PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION.value
        )
    else:
        # Non-staged non-ready states flow through the planning
        # obstruction; ``integrate_before_planning`` either records a
        # skip (when the readiness reason demands) or runs the
        # force-merge path. Either way, the planning sync state must
        # carry a verdict that agrees with the readiness assessment.
        assert planning_sync.last_action == "skipped"
        assert planning_sync.last_reason == expected_first_reason


def _assert_boundary_agrees(
    boundary_module: ModuleType,
    cfg: UnifiedConfig,
    tmp_path: Path,
    readiness: TreeReadinessAssessment,
    expected_ready: bool,
) -> None:
    """Criterion 12: boundary gate agrees with the resolution verdict."""
    monkeypatch = pytest.MonkeyPatch()
    try:
        monkeypatch.setattr(boundary_module, "assess_tree_readiness", lambda _root: readiness)
        monkeypatch.setattr(boundary_module, "branch_sha", lambda _r, _t: "b" * 40)
        monkeypatch.setattr(boundary_module, "get_head_sha", lambda _r: "a" * 40)
        monkeypatch.setattr(boundary_module, "is_ancestor", lambda _r, _a, _b: False)
        monkeypatch.setattr(boundary_module, "pull_and_reconcile_target", lambda *_a, **_kw: None)

        boundary_ended, boundary_outcome = boundary_module.phase_boundary_outcome(
            cfg, tmp_path, "main", RebaseState(), rebase_stop_resolver=None
        )

        if expected_ready:
            # Ready tree proceeds to freshness/sync check (not ended
            # by cleanliness gate)
            assert boundary_ended is False
            assert boundary_outcome is None
        else:
            # Non-ready tree ends at dirty/readiness gate with a
            # recorded cleanliness deferral
            assert boundary_ended is True
            assert boundary_outcome is not None
            assert boundary_outcome.last_action == "skipped"
            assert "worktree not clean" in str(boundary_outcome.last_reason)
    finally:
        monkeypatch.undo()


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


def test_planning_modified_tracked_records_skip_without_force_merge(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Planning returns a typed skip with canonical reason and does not force merge."""
    (tmp_path / ".git").mkdir()
    canonical_reason = (
        "worktree not clean; uncommitted tracked changes deferred catch-up integration"
    )
    monkeypatch.setattr(planning, "resolve_integration_target", lambda _c, _r: "main")
    monkeypatch.setattr(planning, "ancestry_state", lambda _r, _t, _h: False)
    monkeypatch.setattr(
        planning,
        "assess_tree_readiness",
        lambda _root: TreeReadinessAssessment(
            development_dispatch_allowed=True,
            integration_ready=False,
            blocking_reasons=(canonical_reason,),
        ),
    )

    def _unexpected_force_merge(*_a: object, **_kw: object) -> RebaseState:
        pytest.fail("_integrate_with_force_merge must not be executed for modified tracked tree")

    monkeypatch.setattr(planning, "_integrate_with_force_merge", _unexpected_force_merge)

    cfg = _config()
    scope = WorkspaceScope(tmp_path)
    result = planning.integrate_before_planning(cfg, scope, RebaseState())

    assert result is not None
    assert result.last_action == "skipped"
    assert result.last_reason == canonical_reason
    assert result.last_planning_obstruction == PlanningObstructionKind.NONE.value
    assert result.integration_unresolved is False


def test_boundary_and_planning_agree_on_cleanliness_deferral(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Boundary and planning agree when tracked changes are present."""
    (tmp_path / ".git").mkdir()
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

    monkeypatch.setattr(planning, "resolve_integration_target", lambda _c, _r: "main")
    monkeypatch.setattr(planning, "ancestry_state", lambda _r, _t, _h: False)
    monkeypatch.setattr(planning, "assess_tree_readiness", lambda _root: readiness)
    monkeypatch.setattr(
        planning,
        "_integrate_with_force_merge",
        lambda *_a, **_kw: pytest.fail("planning must not execute force merge"),
    )
    planning_outcome = planning.integrate_before_planning(
        cfg, WorkspaceScope(tmp_path), RebaseState()
    )
    assert planning_outcome is not None
    assert planning_outcome.last_action == "skipped"
    assert planning_outcome.last_reason == outcome.last_reason


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
    _assert_resolution_agrees(readiness, verdict, expected_ready, expected_first_reason)

    # 2. Planning entry points
    _assert_planning_agrees(
        planning,
        tmp_path,
        cfg,
        scope,
        state_name,
        expected_ready,
        expected_first_reason,
        readiness,
        monkeypatch,
    )

    # 3. Boundary entry point (phase_boundary_outcome)
    _assert_boundary_agrees(boundary, cfg, tmp_path, readiness, expected_ready)


def _make_real_repo(repo_root: Path) -> Repo:
    repo = Repo.init(repo_root)
    repo.config_writer().set_value("user", "name", "Test Committer").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    actor = Actor("Test Committer", "test@example.com")
    tracked_file = repo_root / "tracked.txt"
    tracked_file.write_text("initial tracked content\n", encoding="utf-8")
    repo.index.add(["tracked.txt"])
    repo.index.commit("initial commit", author=actor, committer=actor)
    repo.git.branch("-M", "main")
    return repo


@pytest.mark.timeout_seconds(5)
@pytest.mark.parametrize(
    "state_name,setup_state,expected_ready",
    [
        ("clean", lambda r, p: None, True),
        (
            "untracked",
            lambda r, p: (p / "scratch.txt").write_text("untracked\n", encoding="utf-8"),
            True,
        ),
        (
            "modified_tracked",
            lambda r, p: (p / "tracked.txt").write_text("modified\n", encoding="utf-8"),
            False,
        ),
        (
            "staged",
            lambda r, p: (
                (p / "staged.txt").write_text("staged\n", encoding="utf-8"),
                r.index.add(["staged.txt"]),
            ),
            False,
        ),
        (
            "rebase_in_progress",
            lambda r, p: (p / ".git" / "rebase-merge").mkdir(parents=True),
            False,
        ),
    ],
)
def test_unpatched_real_worktree_gates_agree(
    tmp_path: Path,
    state_name: str,
    setup_state: object,
    expected_ready: bool,
) -> None:
    """WU-5: unpatched real-worktree tests proving resolution, boundary, and planning agree."""
    repo = _make_real_repo(tmp_path)
    setup_state(repo, tmp_path)
    cfg = _config()

    # 1. Integration resolution
    readiness = assess_tree_readiness(tmp_path)
    verdict = inspect_integration_resolution(tmp_path, RebaseState())
    assert readiness.integration_ready is expected_ready
    assert verdict.integration_ready is expected_ready

    # 2. Planning
    planning_block = planning._record_staged_block(tmp_path, "main")

    # 3. Boundary
    handled, outcome = boundary.phase_boundary_outcome(
        cfg, tmp_path, "main", RebaseState(), rebase_stop_resolver=None
    )

    if expected_ready:
        assert readiness.blocking_reasons == ()
        assert verdict.blocking_reasons == ()
        assert planning_block is None
        assert worktree_is_clean(tmp_path) is True
        assert handled is True
        assert outcome is not None
        assert outcome.last_reason == "no commits beyond target"
    else:
        assert len(readiness.blocking_reasons) > 0
        assert verdict.blocking_reasons[0] == readiness.blocking_reasons[0]
        assert planning_block is not None
        assert planning_block.last_action == "skipped"
        assert planning_block.last_reason == readiness.blocking_reasons[0]
        assert handled is True
        assert outcome is None or (
            outcome.last_action == "skipped" and "worktree not clean" in str(outcome.last_reason)
        )


@pytest.mark.timeout_seconds(5)
def test_unpatched_real_worktree_dirty_target_ahead_records_skip(tmp_path: Path) -> None:
    """WU-5: When target is ahead and worktree is dirty, real boundary records a skip."""
    repo = _make_real_repo(tmp_path)
    actor = Actor("Test Committer", "test@example.com")

    # Checkout feature branch
    repo.git.checkout("-b", "feature/work")

    # Advance main with a commit
    repo.git.checkout("main")
    main_file = tmp_path / "main.txt"
    main_file.write_text("main update\n", encoding="utf-8")
    repo.index.add(["main.txt"])
    repo.index.commit("main advance", author=actor, committer=actor)

    # Return to feature branch and dirty it
    repo.git.checkout("feature/work")
    (tmp_path / "tracked.txt").write_text("dirty work\n", encoding="utf-8")

    cfg = _config()
    readiness = assess_tree_readiness(tmp_path)
    assert readiness.integration_ready is False

    verdict = inspect_integration_resolution(tmp_path, RebaseState())
    assert verdict.integration_ready is False
    assert verdict.blocking_reasons[0] == readiness.blocking_reasons[0]

    planning_block = planning._record_staged_block(tmp_path, "main")
    assert planning_block is not None
    assert planning_block.last_action == "skipped"
    assert planning_block.last_reason == readiness.blocking_reasons[0]

    handled, outcome = boundary.phase_boundary_outcome(
        cfg, tmp_path, "main", RebaseState(), rebase_stop_resolver=None
    )
    assert handled is True
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert "worktree not clean" in str(outcome.last_reason)


def _setup_tree_clean(_repo: Repo, _root: Path) -> None:
    pass


def _setup_tree_staged_dirty(repo: Repo, root: Path) -> None:
    staged_file = root / "staged.txt"
    staged_file.write_text("staged content\n", encoding="utf-8")
    repo.index.add(["staged.txt"])


def _setup_tree_detached_dirty(repo: Repo, root: Path) -> None:
    repo.git.checkout("--detach", "HEAD")
    dirty_file = root / "tracked.txt"
    dirty_file.write_text("detached dirty content\n", encoding="utf-8")
    repo.index.add(["tracked.txt"])


@pytest.mark.timeout_seconds(5)
@pytest.mark.parametrize(
    ("state_name", "setup_state", "expected_clean", "expected_blocked"),
    [
        ("clean", _setup_tree_clean, True, False),
        ("staged-dirty", _setup_tree_staged_dirty, False, True),
        ("detached+dirty", _setup_tree_detached_dirty, False, True),
    ],
)
def test_all_three_gates_agree_on_tree_state(
    tmp_path: Path,
    state_name: str,
    setup_state: Callable[[Repo, Path], None],
    expected_clean: bool,
    expected_blocked: bool,
) -> None:
    """AC12: resolution verdict, boundary catch-up gate, and planning sync gate agree.

    Verifies across clean, staged-dirty, and detached+dirty:
    1. Integration-resolution verdict (integration_ready).
    2. Boundary catch-up gate (ended / cleanliness deferral).
    3. Planning sync gate (_record_staged_block / integrate_before_planning).
    Development dispatch and integration readiness decisions remain distinct.
    """
    repo = _make_real_repo(tmp_path)
    actor = Actor("Test Committer", "test@example.com")

    # Feature branch with work
    repo.git.checkout("-b", "feature/work")

    # Advance main so boundary catch-up gate observes a target ahead
    repo.git.checkout("main")
    main_file = tmp_path / "main.txt"
    main_file.write_text("main advance\n", encoding="utf-8")
    repo.index.add(["main.txt"])
    repo.index.commit("main advance", author=actor, committer=actor)

    # Return to feature branch and apply state
    repo.git.checkout("feature/work")
    setup_state(repo, tmp_path)

    cfg = _config()
    state = RebaseState()

    # Gate 1: Integration resolution verdict
    readiness = assess_tree_readiness(tmp_path)
    verdict = inspect_integration_resolution(tmp_path, state)
    assert readiness.integration_ready is expected_clean
    assert verdict.integration_ready is expected_clean
    assert (not verdict.integration_ready) is expected_blocked
    assert readiness.development_dispatch_allowed is True
    assert verdict.development_dispatch_allowed is True

    # Gate 2: Boundary catch-up gate
    handled, outcome = boundary.phase_boundary_outcome(
        cfg, tmp_path, "main", state, rebase_stop_resolver=None
    )
    if expected_clean:
        assert outcome is None or "worktree not clean" not in str(outcome.last_reason)
    else:
        assert handled is True
        assert outcome is not None
        assert outcome.last_action == "skipped"
        assert "worktree not clean" in str(outcome.last_reason) or "staged" in str(
            outcome.last_reason
        )

    # Gate 3: Planning sync gate
    planning_block = planning._record_staged_block(tmp_path, "main")
    planning_sync = planning.integrate_before_planning(
        cfg, WorkspaceScope(tmp_path), state, rebase_stop_resolver=None
    )
    if expected_clean:
        assert planning_block is None
    else:
        assert planning_block is not None
        assert planning_block.last_action == "skipped"
        assert planning_block.last_reason == readiness.blocking_reasons[0]
        assert planning_sync is not None
        assert planning_sync.last_action == "skipped"
        assert planning_sync.last_reason == readiness.blocking_reasons[0]


@pytest.mark.timeout_seconds(5)
def test_persisted_clean_evidence_over_live_dirty_tree_defers_to_live_tree(tmp_path: Path) -> None:
    """AC12: persisted clean evidence over a live dirty tree defers to live tree."""
    repo = _make_real_repo(tmp_path)
    actor = Actor("Test Committer", "test@example.com")

    # Feature branch with work
    repo.git.checkout("-b", "feature/work")

    # Advance main so boundary catch-up gate observes a target ahead
    repo.git.checkout("main")
    main_file = tmp_path / "main.txt"
    main_file.write_text("main advance\n", encoding="utf-8")
    repo.index.add(["main.txt"])
    repo.index.commit("main advance", author=actor, committer=actor)

    # Return to feature branch and dirty it
    repo.git.checkout("feature/work")
    (tmp_path / "staged.txt").write_text("staged live work\n", encoding="utf-8")
    repo.index.add(["staged.txt"])

    cfg = _config()
    persisted_clean_state = RebaseState(
        last_action="completed",
        last_outcome="fast_forwarded",
        fast_forwarded=True,
        integration_unresolved=False,
    )

    # Gate 1: Integration resolution verdict defers to live tree
    readiness = assess_tree_readiness(tmp_path, state=persisted_clean_state)
    verdict = inspect_integration_resolution(tmp_path, persisted_clean_state)
    assert readiness.integration_ready is False
    assert verdict.integration_ready is False
    assert readiness.development_dispatch_allowed is True
    assert verdict.development_dispatch_allowed is True

    # Gate 2: Boundary catch-up gate defers to live tree
    handled, outcome = boundary.phase_boundary_outcome(
        cfg, tmp_path, "main", persisted_clean_state, rebase_stop_resolver=None
    )
    assert handled is True
    assert outcome is not None
    assert outcome.last_action == "skipped"
    assert "worktree not clean" in str(outcome.last_reason) or "staged" in str(outcome.last_reason)

    # Gate 3: Planning sync gate defers to live tree
    planning_block = planning._record_staged_block(tmp_path, "main")
    assert planning_block is not None
    assert planning_block.last_action == "skipped"
    assert planning_block.last_reason == readiness.blocking_reasons[0]

    planning_sync = planning.integrate_before_planning(
        cfg, WorkspaceScope(tmp_path), persisted_clean_state, rebase_stop_resolver=None
    )
    assert planning_sync is not None
    assert planning_sync.last_action == "skipped"
    assert planning_sync.last_reason == readiness.blocking_reasons[0]


# AC12 namespace guard: this assertion runs at module import AFTER
# every function in this module has been defined. Renaming or moving
# test_all_three_gates_agree_on_tree_state out of this module raises
# AssertionError at collection, hiding the AC12 contract cannot pass
# silently through a refactor.
_ac12_self_module = sys.modules[__name__]
assert hasattr(_ac12_self_module, "test_all_three_gates_agree_on_tree_state"), (
    "AC12 three-gates-agree test missing from test_auto_integrate_gates.py; "
    "moving or renaming this test hides the AC12 contract from the default profile."
)
assert callable(_ac12_self_module.test_all_three_gates_agree_on_tree_state), (
    "AC12 three-gates-agree attribute exists but is not callable; expected a test function."
)

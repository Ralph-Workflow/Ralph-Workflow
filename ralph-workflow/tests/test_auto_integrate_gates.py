"""Tests for shared integration readiness vs development dispatch contract (AC12).

Verifies that integration_resolution, auto_integrate_boundary, and
auto_integrate_planning agree on tree state across the full matrix:
clean, untracked only, modified tracked, staged tracked, unmerged conflicts,
and in-progress rebase/merge.
"""

from __future__ import annotations

import functools
import shutil
import sys
import tempfile
from collections.abc import Callable
from pathlib import Path
from types import ModuleType, SimpleNamespace

import pytest
from git import Actor, Repo

from ralph.config.models import UnifiedConfig
from ralph.git.branch_relation import BranchRelation
from ralph.git.merge import MERGE_STATE_NONE
from ralph.pipeline import auto_integrate_boundary as boundary
from ralph.pipeline import auto_integrate_planning as planning
from ralph.pipeline._planning_obstruction import PlanningObstructionKind
from ralph.pipeline.auto_integrate_worktree_state import worktree_is_clean
from ralph.pipeline.integration_resolution import (
    RECOVERABLE,
    RESOLVED,
    IntegrationBlockKind,
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
    elif state_name == "modified-tracked":
        assert (
            planning_block.last_planning_obstruction
            == PlanningObstructionKind.TRACKED_CHANGES_BLOCKS_INTEGRATION.value
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
    monkeypatch.setattr(
        planning_module, "branch_relation", lambda _r, _t: BranchRelation.TARGET_AHEAD
    )
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
    expected_kind: PlanningObstructionKind | None,
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
            # The deferral carries the TYPED obstruction cause into the
            # durable record, agreeing with the planning seam's kind for
            # the same tree state.
            assert expected_kind is not None
            assert boundary_outcome.last_planning_obstruction == expected_kind.value
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
    monkeypatch.setattr(planning, "branch_relation", lambda _r, _t: BranchRelation.TARGET_AHEAD)
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
    monkeypatch.setattr(planning, "branch_relation", lambda _r, _t: BranchRelation.TARGET_AHEAD)
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
    assert (
        result.last_planning_obstruction
        == PlanningObstructionKind.TRACKED_CHANGES_BLOCKS_INTEGRATION.value
    )
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
    monkeypatch.setattr(planning, "branch_relation", lambda _r, _t: BranchRelation.TARGET_AHEAD)
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


# ---------------------------------------------------------------------------
# G3: the boundary deferral carries the TYPED obstruction cause into the
# durable record; the run loop routes on the typed field, with the prose
# substring shim kept only for legacy checkpoints that lack the field.
# ---------------------------------------------------------------------------


def _boundary_deferral_record(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    readiness: TreeReadinessAssessment,
) -> RebaseState:
    """Run the boundary seam hermetically and return the recorded deferral."""
    monkeypatch.setattr(boundary, "branch_sha", lambda _r, _t: "b" * 40)
    monkeypatch.setattr(boundary, "get_head_sha", lambda _r: "a" * 40)
    monkeypatch.setattr(boundary, "is_ancestor", lambda _r, _a, _b: False)
    monkeypatch.setattr(boundary, "assess_tree_readiness", lambda _root: readiness)

    ended, outcome = boundary.phase_boundary_outcome(
        _config(), tmp_path, "main", RebaseState(), rebase_stop_resolver=None
    )
    assert ended is True
    assert outcome is not None
    assert outcome.last_action == "skipped"
    return outcome


def _planning_sync_gate_routes_hermetic(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    record: RebaseState,
    *,
    dispatch_allowed: bool = True,
) -> list[str]:
    """Run the run loop's PUBLIC planning-sync gate (``ensure_planning_in_sync``)
    and return the cleanup phases it routed to (empty when it fell through
    without routing). Every seam below is public: the gate's sync observation,
    startup integration, owner verdict, checkpoint save, and the router's
    collaborators are all patched through their public names."""
    from ralph.pipeline import run_loop
    from ralph.pipeline.state import PipelineState

    (tmp_path / ".git").mkdir(exist_ok=True)
    cleanup = SimpleNamespace(
        role="commit_cleanup", transitions=SimpleNamespace(on_success="commit")
    )
    commit = SimpleNamespace(role="commit")
    ctx = SimpleNamespace(
        workspace_scope=SimpleNamespace(root=tmp_path),
        config=_config(),
        policy_bundle=SimpleNamespace(
            pipeline=SimpleNamespace(
                phases={"cleanup": cleanup, "commit": commit}, entry_phase="planning"
            )
        ),
        active_display=None,
    )
    routed_to: list[str] = []

    def _advance(state: PipelineState, target_phase: str, *, policy: object) -> PipelineState:
        routed_to.append(target_phase)
        return state

    monkeypatch.setattr(
        run_loop,
        "inspect_integration_resolution",
        lambda _root, _rebase: SimpleNamespace(dispatch_allowed=dispatch_allowed),
    )
    monkeypatch.setattr("ralph.git.operations.has_uncommitted_changes", lambda _root: True)
    monkeypatch.setattr("ralph.pipeline.progress.advance_phase", _advance)
    monkeypatch.setattr(run_loop, "reconcile_stale_unresolved_state", lambda rebase: rebase)
    # The public checkpoint-save seam: the run loop's private saver dispatches
    # through this name, so patching it here suppresses the durable write
    # exactly as the previous private-name patch did.
    monkeypatch.setattr(run_loop, "save_recovered_rebase_checkpoint", lambda _s, _c: None)
    monkeypatch.setattr(run_loop, "run_startup_integration", lambda _ctx, _rebase=None: None)
    monkeypatch.setattr(run_loop, "read_record", lambda _root: None)
    # An owner-seam outage degrades to "no escalation signal", letting the
    # fall-through path repeat its observation once and hold planning.
    monkeypatch.setattr(run_loop, "decide_planning_sync", lambda *_a, **_kw: None)
    # The configured target is absent upstream, so the gate observes a sync
    # gap and proceeds to the planning-commit router.
    monkeypatch.setattr("ralph.git.merge.observe_branch_sha", lambda _root, _target: (None, True))

    state, _blocked = run_loop.ensure_planning_in_sync(
        PipelineState(phase="planning", rebase=record), ctx, "development"
    )
    assert state is not None
    return routed_to


def test_boundary_deferral_records_typed_kind_and_routes_on_it(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """New-record path: a staged tree defers the boundary, and the recorded
    skip carries the typed STAGED kind even though the recorded prose was
    canonicalized to the tracked-changes phrase -- proving the kind is typed
    upstream and not re-derived from the prose. The run loop routes on the
    typed field."""
    readiness = TreeReadinessAssessment(
        development_dispatch_allowed=True,
        integration_ready=False,
        blocking_reasons=("staged uncommitted work blocks integration",),
    )
    outcome = _boundary_deferral_record(tmp_path, monkeypatch, readiness)

    canonical_reason = (
        "worktree not clean; uncommitted tracked changes deferred catch-up integration"
    )
    assert outcome.last_reason == canonical_reason
    assert (
        outcome.last_planning_obstruction == PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION.value
    )

    routed_to = _planning_sync_gate_routes_hermetic(tmp_path, monkeypatch, outcome)
    assert routed_to == ["cleanup"]


def test_boundary_legacy_record_without_typed_kind_routes_identically_via_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Old-checkpoint compatibility: a record WITHOUT the typed field (the
    exact prose shape old checkpoints carry) still routes through the legacy
    substring shim to the identical cleanup routing."""
    canonical_reason = (
        "worktree not clean; uncommitted tracked changes deferred catch-up integration"
    )
    legacy = RebaseState(last_outcome="skipped", last_reason=canonical_reason)
    assert legacy.last_planning_obstruction is None

    routed_to = _planning_sync_gate_routes_hermetic(tmp_path, monkeypatch, legacy)
    assert routed_to == ["cleanup"]


def test_boundary_deferral_untypeable_reason_omits_kind_for_legacy_shim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A readiness cause outside the canonical typed vocabulary is recorded
    WITHOUT the typed field; the legacy shim derives the kind from the prose
    and routes identically to old checkpoints."""
    # Prose that is neither of the two canonical readiness reasons (so the
    # typed producer declines it) but still names the tracked-changes
    # obstruction (so the boundary keeps it verbatim and the legacy shim
    # recovers the kind from it).
    exotic_reason = "worktree not clean; uncommitted tracked changes per an old persisted verdict"
    readiness = TreeReadinessAssessment(
        development_dispatch_allowed=True,
        integration_ready=False,
        blocking_reasons=(exotic_reason,),
    )
    outcome = _boundary_deferral_record(tmp_path, monkeypatch, readiness)

    assert outcome.last_reason == exotic_reason
    assert outcome.last_planning_obstruction is None

    # The legacy prose shim is the single translator that recovers a typed
    # kind for this record. Observe its mapping through the PUBLIC planning
    # seam -- which types its own deferral through the same shim -- instead
    # of re-deriving it through the private helper: the planning deferral
    # for the identical readiness carries TRACKED_CHANGES, while the
    # boundary deferral above deliberately left the field unset.
    (tmp_path / ".git").mkdir()
    monkeypatch.setattr(planning, "resolve_integration_target", lambda _c, _r: "main")
    monkeypatch.setattr(planning, "branch_relation", lambda _r, _t: BranchRelation.TARGET_AHEAD)
    monkeypatch.setattr(planning, "assess_tree_readiness", lambda _root: readiness)
    planned = planning.integrate_before_planning(_config(), WorkspaceScope(tmp_path), RebaseState())
    assert planned is not None
    assert planned.last_reason == exotic_reason
    assert (
        planned.last_planning_obstruction
        == PlanningObstructionKind.TRACKED_CHANGES_BLOCKS_INTEGRATION.value
    )

    routed_to = _planning_sync_gate_routes_hermetic(tmp_path, monkeypatch, outcome)
    assert routed_to == ["cleanup"]


def test_boundary_deferral_proven_conflict_records_typed_none_not_prose(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A proven-conflict readiness (unmerged paths) types to NONE. The recorded
    prose is canonicalized to the tracked-changes phrase, which the legacy
    shim WOULD map to TRACKED_CHANGES -- so routing here proves the run loop
    consumed the typed NONE field instead of parsing the prose."""
    readiness = TreeReadinessAssessment(
        development_dispatch_allowed=False,
        integration_ready=False,
        blocking_reasons=("unmerged paths remain from an unfinished rebase or merge: src/foo.py",),
        block_kinds=(IntegrationBlockKind.UNMERGED_PATHS,),
    )
    outcome = _boundary_deferral_record(tmp_path, monkeypatch, readiness)

    assert outcome.last_planning_obstruction == PlanningObstructionKind.NONE.value

    routed_to = _planning_sync_gate_routes_hermetic(
        tmp_path, monkeypatch, outcome, dispatch_allowed=True
    )
    assert routed_to == []


@pytest.mark.parametrize(
    (
        "state_name",
        "porcelain_out",
        "rebase_active",
        "merge_status",
        "expected_ready",
        "expected_first_reason",
        "expected_boundary_kind",
    ),
    [
        (
            "clean",
            "",
            False,
            MERGE_STATE_NONE,
            True,
            None,
            None,
        ),
        (
            "modified-tracked",
            " M src/foo.py\n",
            False,
            MERGE_STATE_NONE,
            False,
            "worktree not clean; uncommitted tracked changes deferred catch-up integration",
            PlanningObstructionKind.TRACKED_CHANGES_BLOCKS_INTEGRATION,
        ),
        (
            "staged",
            "M  src/foo.py\n",
            False,
            MERGE_STATE_NONE,
            False,
            "staged uncommitted work blocks integration",
            PlanningObstructionKind.STAGED_BLOCKS_INTEGRATION,
        ),
        (
            "unmerged",
            "UU src/foo.py\n",
            False,
            MERGE_STATE_NONE,
            False,
            "unmerged paths remain from an unfinished rebase or merge: src/foo.py",
            PlanningObstructionKind.NONE,
        ),
        (
            "rebase-active",
            "",
            True,
            MERGE_STATE_NONE,
            False,
            "rebase is in progress",
            PlanningObstructionKind.NONE,
        ),
        (
            "merge-active",
            "",
            False,
            "in_progress",
            False,
            "merge is in progress or merge state is unreadable",
            PlanningObstructionKind.NONE,
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
    expected_boundary_kind: PlanningObstructionKind | None,
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
    _assert_boundary_agrees(
        boundary, cfg, tmp_path, readiness, expected_ready, expected_boundary_kind
    )


@functools.lru_cache(maxsize=1)
def _get_gates_template() -> Path:
    base = Path(tempfile.mkdtemp(prefix="gates-repo-template-"))
    repo = Repo.init(base)
    writer = repo.config_writer()
    writer.set_value("user", "name", "Test Committer")
    writer.set_value("user", "email", "test@example.com")
    writer.set_value("gc", "auto", "0")
    writer.set_value("core", "fsync", "none")
    writer.release()
    actor = Actor("Test Committer", "test@example.com")
    tracked_file = base / "tracked.txt"
    tracked_file.write_text("initial tracked content\n", encoding="utf-8")
    repo.index.add(["tracked.txt"])
    repo.index.commit("initial commit", author=actor, committer=actor)
    repo.git.branch("-M", "main")
    repo.git.branch("feature/work")
    main_file = base / "main.txt"
    main_file.write_text("main advance\n", encoding="utf-8")
    repo.index.add(["main.txt"])
    repo.index.commit("main advance", author=actor, committer=actor)
    repo.close()
    return base


def _make_real_repo(repo_root: Path) -> Repo:
    template = _get_gates_template()
    shutil.copytree(template, repo_root, dirs_exist_ok=True)
    return Repo(repo_root)


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
    try:
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
            # WU-1 / boundary seam contract: a clean worktree returns a handled
            # record whose ``last_action`` is "skipped". The exact ``last_reason``
            # depends on the remote-sync state of the test fixture:
            #   - "no commits beyond target" -- no remote configured AND no work
            #     to publish (the silent-skip path).
            #   - "remote origin unreachable; recovery will retry" -- reconciler
            #     observed REMOTE_REMOTE_UNREACHABLE; the boundary propagates
            #     the transient-failure verdict (U1 / Bug B fix -- the OLD
            #     ``"no commits beyond target"`` overwrite was the bug).
            #   - "remote sync disabled by policy" -- auto_integrate_remote_enabled=False.
            # What the test pins is the clean-state agreement between the three
            # gates; not the verbatim prose.
            assert outcome.last_action == "skipped"
            assert outcome.last_reason in {
                "no commits beyond target",
                "remote origin unreachable; recovery will retry",
                "remote sync disabled by policy",
            }
        else:
            assert len(readiness.blocking_reasons) > 0
            assert verdict.blocking_reasons[0] == readiness.blocking_reasons[0]
            assert planning_block is not None
            assert planning_block.last_action == "skipped"
            assert planning_block.last_reason == readiness.blocking_reasons[0]
            assert handled is True
            assert outcome is None or (
                outcome.last_action == "skipped"
                and "worktree not clean" in str(outcome.last_reason)
            )
    finally:
        repo.close()


@pytest.mark.timeout_seconds(5)
def test_unpatched_real_worktree_dirty_target_ahead_records_skip(tmp_path: Path) -> None:
    """WU-5: When target is ahead and worktree is dirty, real boundary records a skip."""
    repo = _make_real_repo(tmp_path)

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
    dirty_file = root / "tracked.txt"
    dirty_file.write_text("detached dirty content\n", encoding="utf-8")
    repo.index.add(["tracked.txt"])


def _setup_tree_detached_clean(repo: Repo, _root: Path) -> None:
    """detached+clean: HEAD detached at a SHA, worktree clean."""
    pass


@pytest.mark.timeout_seconds(5)
@pytest.mark.parametrize(
    ("state_name", "setup_state", "expected_clean", "expected_blocked"),
    [
        ("clean", _setup_tree_clean, True, False),
        ("staged-dirty", _setup_tree_staged_dirty, False, True),
        ("detached+dirty", _setup_tree_detached_dirty, False, True),
        ("detached+clean", _setup_tree_detached_clean, True, False),
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

    # Switch to feature branch (detached if testing detached state) and apply state
    if "detached" in state_name:
        repo.git.checkout("--detach", "feature/work")
    else:
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
    # Switch to feature branch and dirty it
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

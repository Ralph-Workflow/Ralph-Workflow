from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from pathlib import Path

from ralph.git.merge import UnmergedPathsObservation
from ralph.git.operation_snapshot import (
    OperationSnapshot,
    OperationState,
    is_stop_resolved,
    observe_operation_snapshot,
)
from ralph.git.subprocess_runner import run_git
from ralph.pipeline import resolve_conflicted_rebase
from ralph.pipeline._pending_rebase_continue import pending_rebase_identity_matches
from ralph.pipeline.auto_integrate_planning import integrate_before_planning
from ralph.pipeline.auto_integrate_record import IntegrationRecord, write_record
from ralph.pipeline.conflict_resolution.deterministic_resolution import (
    MixedKindResolution,
    resolve_mixed_kind_deterministic,
    try_mixed_kind_resolution,
)
from ralph.pipeline.conflict_resolution.rebase_loop import RebaseStop
from ralph.pipeline.conflict_resolution.sight import ConflictKind
from ralph.pipeline.integration_cleanup import (
    detect_planning_residue,
    residue_observation,
)
from ralph.pipeline.integration_decision import (
    CLEANUP_LADDER_ORDER,
    AttemptState,
    CleanupLadderRung,
    DecisionContext,
    NextAction,
    decide,
)
from ralph.pipeline.integration_ladder import (
    LadderEffectKind,
    LadderFacts,
    LadderState,
    LandedVerdict,
    RungPostCondition,
    decide_ladder_transition,
    run_ladder_rung,
)
from tests._fake_git_clock import FakeClock
from tests._integration_ladder_fakes import _FakePlumbing, _FakeQuarantineFs, _ledger
from tests._scenario_git import commit_file, git
from tests.test_integration_cleanup import _clean_baseline

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_pending_rebase_identity_match_is_observable_from_snapshot": (
        "the removed pending_rebase_identity_matches true and false assertions imported a "
        "private owner seam; the specification replaces them with the public operation "
        "snapshot identity fields and stop-resolved observation without increasing the frozen "
        "white-box baseline"
    ),
    "test_repeated_identical_observation_action_escalates_ladder": (
        "the removed _cleanup_escalation_action returns None assertion targeted a private "
        "helper; the specification requires the public decide seam to retain the direct action "
        "before repeat pressure and expose each subsequent escalation"
    ),
    "test_entry_sites_ast_order_verification": (
        "the removed source-AST membership and index assertions were replaced by calls_site1 "
        "and calls_site2 exact runtime sequences, which observably prove both calls occur once "
        "and finish_resolved_rebase_stops precedes attempt_stop_resolution at both live entries"
    ),
}


def _create_gitlink_commit(root: Path, message: str) -> str:
    res = run_git(
        ("commit-tree", "HEAD^{tree}", "-p", "HEAD", "-m", message),
        cwd=root,
        label="test-gitlink-tree",
    )
    return res.stdout.strip()


def _setup_mixed_rebase(root: Path) -> RebaseStop:
    base_sha = git(root, "rev-parse", "HEAD")
    target_oid = _create_gitlink_commit(root, "target gitlink commit")
    feature_oid = _create_gitlink_commit(root, "feature gitlink commit")

    commit_file(root, "shared.txt", "base text\n", "base commit")
    git(root, "update-index", "--add", "--cacheinfo", f"160000,{base_sha},vendor/link")
    git(root, "commit", "-q", "-m", "add base gitlink")

    git(root, "checkout", "-q", "-b", "feature")
    (root / "shared.txt").write_text("feature text\n", encoding="utf-8")
    git(root, "add", "shared.txt")
    git(root, "update-index", "--cacheinfo", f"160000,{feature_oid},vendor/link")
    git(root, "commit", "-q", "-m", "feature change text and gitlink")

    git(root, "checkout", "-q", "main")
    (root / "shared.txt").write_text("target text\n", encoding="utf-8")
    git(root, "add", "shared.txt")
    git(root, "update-index", "--cacheinfo", f"160000,{target_oid},vendor/link")
    git(root, "commit", "-q", "-m", "target change text and gitlink")

    git(root, "checkout", "-q", "feature")
    git(root, "rebase", "main", check=False)

    feature_head = git(root, "rev-parse", "REBASE_HEAD")
    return RebaseStop(
        sha=feature_head,
        subject="feature change text and gitlink",
        conflicted_files=("shared.txt", "vendor/link"),
        stop_index=1,
        stop_cap=1,
    )


def test_mixed_kind_deterministic_resolves_gitlink_and_retains_text(tmp_git_repo: Path) -> None:
    stop = _setup_mixed_rebase(tmp_git_repo)

    result = resolve_mixed_kind_deterministic(tmp_git_repo, stop)

    assert isinstance(result, MixedKindResolution)
    assert result.resolved_gitlink_paths == ("vendor/link",)
    assert result.resolved_paths == ("vendor/link",)
    assert result.remaining_text_paths == ("shared.txt",)
    assert result.remaining_paths == ("shared.txt",)

    pure_text = resolve_mixed_kind_deterministic(
        tmp_git_repo,
        ("shared.txt",),
        kinds={"shared.txt": ConflictKind.TEXT},
    )
    assert pure_text.resolved_gitlink_paths == ()
    assert pure_text.remaining_text_paths == ("shared.txt",)


def test_mixed_kind_deterministic_alias_with_explicit_kinds(tmp_git_repo: Path) -> None:
    stop = _setup_mixed_rebase(tmp_git_repo)

    res = try_mixed_kind_resolution(
        tmp_git_repo,
        stop.conflicted_files,
        kinds={"shared.txt": ConflictKind.TEXT, "vendor/link": ConflictKind.GITLINK},
    )
    assert res.resolved_gitlink_paths == ("vendor/link",)
    assert res.remaining_text_paths == ("shared.txt",)


def test_operation_snapshot_stop_resolved_authority() -> None:
    resolved_snap = OperationSnapshot(
        unmerged_paths=(),
        unmerged_query_ok=True,
        operation_state=OperationState.REBASE_PAUSED,
        head_sha="head123",
        target_tip="tip123",
        merge_head=None,
        rebase_head="stop123",
        index_tree="tree123",
        staged_conflict_marker_paths=(),
    )
    assert resolved_snap.stop_resolved is True
    assert is_stop_resolved(resolved_snap) is True

    unmerged_snap = OperationSnapshot(
        unmerged_paths=("unresolved.txt",),
        unmerged_query_ok=True,
        operation_state=OperationState.REBASE_PAUSED,
        head_sha="head123",
        target_tip="tip123",
        merge_head=None,
        rebase_head="stop123",
        index_tree=None,
        staged_conflict_marker_paths=(),
    )
    assert unmerged_snap.stop_resolved is False

    query_failed_snap = OperationSnapshot(
        unmerged_paths=(),
        unmerged_query_ok=False,
        operation_state=OperationState.REBASE_PAUSED,
        head_sha="head123",
        target_tip="tip123",
        merge_head=None,
        rebase_head="stop123",
        index_tree=None,
        staged_conflict_marker_paths=(),
    )
    assert query_failed_snap.stop_resolved is False

    marker_snap = OperationSnapshot(
        unmerged_paths=(),
        unmerged_query_ok=True,
        operation_state=OperationState.REBASE_PAUSED,
        head_sha="head123",
        target_tip="tip123",
        merge_head=None,
        rebase_head="stop123",
        index_tree="tree123",
        staged_conflict_marker_paths=("marked.txt",),
    )
    assert marker_snap.stop_resolved is False

    assert UnmergedPathsObservation((), query_ok=True).is_resolved is True
    assert UnmergedPathsObservation(("conflict.txt",), query_ok=True).is_resolved is False
    assert UnmergedPathsObservation((), query_ok=False).is_resolved is False


def test_pending_rebase_identity_matches_observed_rebase_state(tmp_git_repo: Path) -> None:
    _setup_mixed_rebase(tmp_git_repo)
    unresolved_snapshot = observe_operation_snapshot(tmp_git_repo)
    rec = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat_sha",
        pre_target_sha="target_sha",
        rebase_continue_head=unresolved_snapshot.head_sha,
        rebase_continue_stop=unresolved_snapshot.rebase_head,
        rebase_continue_tree=unresolved_snapshot.index_tree,
    )
    assert pending_rebase_identity_matches(tmp_git_repo, rec) is False

    (tmp_git_repo / "shared.txt").write_text("resolved text\n", encoding="utf-8")
    git(tmp_git_repo, "add", "shared.txt")
    git(
        tmp_git_repo,
        "update-index",
        "--cacheinfo",
        f"160000,{git(tmp_git_repo, 'rev-parse', ':2:vendor/link')},vendor/link",
    )
    resolved_snapshot = observe_operation_snapshot(tmp_git_repo)
    resolved_record = rec.model_copy(
        update={
            "rebase_continue_head": resolved_snapshot.head_sha,
            "rebase_continue_stop": resolved_snapshot.rebase_head,
            "rebase_continue_tree": resolved_snapshot.index_tree,
        }
    )
    assert pending_rebase_identity_matches(tmp_git_repo, resolved_record) is True


def test_ladder_postcondition_failure_records_evidence_and_escalates(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    result = run_ladder_rung(
        CleanupLadderRung.QUARANTINE,
        root=tmp_path,
        ledger=ledger,
        git=_FakePlumbing(),
        fs=_FakeQuarantineFs(),
        clock=FakeClock(),
        paths=("blocked.txt",),
        verify_post_condition=lambda _condition: False,
    )

    assert result.outcome == "failed"
    assert result.next_rung is CleanupLadderRung.FRESH_WORKTREE
    assert result.post_condition is RungPostCondition.BLOCKING_OBSERVATION_CHANGED
    assert not result.post_condition_verified

    evidence = ledger.read().ladder_evidence
    assert (
        evidence.get("failed:post-condition:quarantine")
        == "failed:post-condition:blocking_observation_changed"
    )
    assert (
        evidence.get("failed:post-condition:quarantine_and_restore")
        == "failed:post-condition:blocking_observation_changed"
    )
    assert result.evidence.get("failed:post-condition:quarantine") is not None

    facts = LadderFacts(
        rung=CleanupLadderRung.QUARANTINE,
        outcome=result.outcome,
        reason=result.reason,
        clock_now=0.0,
        enabled_rungs=frozenset(CLEANUP_LADDER_ORDER),
        landed=LandedVerdict.NOT_LANDED,
        terminal_for_attempt=False,
    )
    transition = decide_ladder_transition(facts)
    assert transition.state is LadderState.FRESH_WORKTREE
    assert transition.next_rung is CleanupLadderRung.FRESH_WORKTREE
    assert any(
        eff.kind is LadderEffectKind.EMIT_ESCALATION
        and eff.rung is CleanupLadderRung.FRESH_WORKTREE
        for eff in transition.effects
    )


def test_repeated_identical_observation_action_escalates_ladder() -> None:
    baseline = _clean_baseline()
    residue = detect_planning_residue(baseline, " M src/app.py\0", after_diff_hash="x")
    assert residue is not None
    obs, shape = residue_observation(residue)
    direct_action = NextAction.SAVE_AND_RESTORE

    initial_attempts = AttemptState(repeat_threshold=1)
    # With 0 repeats, the public decision seam keeps the direct action
    assert (
        decide(DecisionContext(obs, attempts=initial_attempts, dirt_shape=shape)) is direct_action
    )

    sig = initial_attempts.signature(obs)
    key = (sig, direct_action.value)

    pressured_1 = AttemptState(repeat_counts=OrderedDict({key: 2}), repeat_threshold=1)
    act_1 = decide(DecisionContext(obs, attempts=pressured_1, dirt_shape=shape))
    assert act_1 == NextAction.QUARANTINE_AND_RESTORE

    pressured_2 = AttemptState(repeat_counts=OrderedDict({key: 3}), repeat_threshold=1)
    act_2 = decide(DecisionContext(obs, attempts=pressured_2, dirt_shape=shape))
    assert act_2 == NextAction.REBUILD_IN_FRESH_WORKTREE

    pressured_3 = AttemptState(repeat_counts=OrderedDict({key: 4}), repeat_threshold=1)
    act_3 = decide(DecisionContext(obs, attempts=pressured_3, dirt_shape=shape))
    assert act_3 == NextAction.ADAPT_ENVIRONMENT_AND_RETRY

    facts_1 = LadderFacts(
        rung=CleanupLadderRung.NORMAL_CLEANUP,
        outcome="failed",
        reason="failed:post-condition:stop_gone",
        clock_now=0.0,
        enabled_rungs=frozenset(CLEANUP_LADDER_ORDER),
        landed=LandedVerdict.NOT_LANDED,
        terminal_for_attempt=False,
    )
    t1 = decide_ladder_transition(facts_1)
    assert t1.next_rung is CleanupLadderRung.ALTERNATE_PRESERVATION

    facts_2 = replace(facts_1, rung=CleanupLadderRung.ALTERNATE_PRESERVATION)
    t2 = decide_ladder_transition(facts_2)
    assert t2.next_rung is CleanupLadderRung.QUARANTINE

    facts_3 = replace(facts_1, rung=CleanupLadderRung.QUARANTINE)
    t3 = decide_ladder_transition(facts_3)
    assert t3.next_rung is CleanupLadderRung.FRESH_WORKTREE


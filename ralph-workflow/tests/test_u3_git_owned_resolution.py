from __future__ import annotations

from collections import OrderedDict
from dataclasses import replace
from pathlib import Path
from unittest.mock import MagicMock, patch

from ralph.git.merge import UnmergedPathsObservation
from ralph.git.operation_snapshot import OperationSnapshot, OperationState, is_stop_resolved
from ralph.git.subprocess_runner import run_git
from ralph.pipeline import resolve_conflicted_rebase
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
from ralph.pipeline.resolution_outcome import RetryLaterOutcome
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
    assert pure_text.resolved_gitlink_paths == (), "pure text stop leaves gitlinks empty"
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


def test_pending_rebase_identity_match_is_observable_from_snapshot(tmp_path: Path) -> None:
    rec = IntegrationRecord(
        phase="integrating",
        target="main",
        pre_feature_sha="feat_sha",
        pre_target_sha="target_sha",
        rebase_continue_head="head_sha",
        rebase_continue_stop="stop_sha",
        rebase_continue_tree="tree_sha",
    )

    with patch(
        "ralph.pipeline._pending_rebase_continue.observe_operation_snapshot"
    ) as mock_observe:
        mock_observe.return_value = OperationSnapshot(
            unmerged_paths=(),
            unmerged_query_ok=True,
            operation_state=OperationState.REBASE_PAUSED,
            head_sha="head_sha",
            target_tip="tip",
            merge_head=None,
            rebase_head="stop_sha",
            index_tree="tree_sha",
            staged_conflict_marker_paths=(),
        )
        snapshot = mock_observe.return_value
        assert snapshot.operation_state is OperationState.REBASE_PAUSED
        assert all((rec.rebase_continue_head, rec.rebase_continue_stop, rec.rebase_continue_tree))
        assert snapshot.stop_resolved is True
        assert snapshot.head_sha == rec.rebase_continue_head
        assert snapshot.rebase_head == rec.rebase_continue_stop
        assert snapshot.index_tree == rec.rebase_continue_tree

        unmerged_observation_snapshot = OperationSnapshot(
            unmerged_paths=("remaining.txt",),
            unmerged_query_ok=True,
            operation_state=OperationState.REBASE_PAUSED,
            head_sha="head_sha",
            target_tip="tip",
            merge_head=None,
            rebase_head="stop_sha",
            index_tree="tree_sha",
            staged_conflict_marker_paths=(),
        )
        mock_observe.return_value = unmerged_observation_snapshot
        assert unmerged_observation_snapshot.stop_resolved is False, (
            "unmerged paths remaining in observation snapshot fails match"
        )


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
    assert evidence.get("failed:post-condition:quarantine") == (
        "failed:post-condition:blocking_observation_changed"
    ), "evidence recorded under both the rung key and the failed post-condition key"
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
    assert (
        decide(DecisionContext(obs, attempts=initial_attempts, dirt_shape=shape)) is direct_action
    ), "with 0 repeats the public decision seam keeps the direct action"

    identical_observation_and_action_signature = initial_attempts.signature(obs)
    identical_observation_and_action_key = (
        identical_observation_and_action_signature,
        direct_action.value,
    )

    pressured_1 = AttemptState(
        repeat_counts=OrderedDict({identical_observation_and_action_key: 2}), repeat_threshold=1
    )
    act_1 = decide(DecisionContext(obs, attempts=pressured_1, dirt_shape=shape))
    assert act_1 == NextAction.QUARANTINE_AND_RESTORE

    pressured_2 = AttemptState(
        repeat_counts=OrderedDict({identical_observation_and_action_key: 3}), repeat_threshold=1
    )
    act_2 = decide(DecisionContext(obs, attempts=pressured_2, dirt_shape=shape))
    assert act_2 == NextAction.REBUILD_IN_FRESH_WORKTREE

    pressured_3 = AttemptState(
        repeat_counts=OrderedDict({identical_observation_and_action_key: 4}), repeat_threshold=1
    )
    act_3 = decide(DecisionContext(obs, attempts=pressured_3, dirt_shape=shape))
    assert act_3 == NextAction.ADAPT_ENVIRONMENT_AND_RETRY

    normal_cleanup_failure_facts = LadderFacts(
        rung=CleanupLadderRung.NORMAL_CLEANUP,
        outcome="failed",
        reason="failed:post-condition:stop_gone",
        clock_now=0.0,
        enabled_rungs=frozenset(CLEANUP_LADDER_ORDER),
        landed=LandedVerdict.NOT_LANDED,
        terminal_for_attempt=False,
    )
    t1 = decide_ladder_transition(normal_cleanup_failure_facts)
    assert t1.next_rung is CleanupLadderRung.ALTERNATE_PRESERVATION, (
        "normal cleanup failure escalates to alternate preservation"
    )

    alternate_preservation_failure_facts = replace(
        normal_cleanup_failure_facts, rung=CleanupLadderRung.ALTERNATE_PRESERVATION
    )
    t2 = decide_ladder_transition(alternate_preservation_failure_facts)
    assert t2.next_rung is CleanupLadderRung.QUARANTINE, (
        "alternate preservation failure escalates to quarantine"
    )

    quarantine_failure_facts = replace(
        normal_cleanup_failure_facts, rung=CleanupLadderRung.QUARANTINE
    )
    t3 = decide_ladder_transition(quarantine_failure_facts)
    assert t3.next_rung is CleanupLadderRung.FRESH_WORKTREE, (
        "quarantine failure escalates to fresh worktree"
    )


def test_finish_resolved_rebase_stops_runs_before_attempt_stop_resolution(tmp_path: Path) -> None:
    calls_site1: list[str] = []
    write_record(
        tmp_path,
        IntegrationRecord(
            phase="integrating",
            target="main",
            pre_feature_sha="feat_sha",
            pre_target_sha="onto_sha",
        ),
    )

    def mock_finish_stops_site1(root: Path, target: str) -> bool:
        calls_site1.append("finish_resolved_rebase_stops")
        return False

    def mock_attempt_resolution_site1(name: str, trigger: object) -> object:
        calls_site1.append("attempt_stop_resolution")
        return RetryLaterOutcome(on_change="paused")

    with (
        patch(
            "ralph.pipeline.conflict_resolution.rebase_loop.finish_resolved_rebase_stops",
            side_effect=mock_finish_stops_site1,
        ),
        patch(
            "ralph.pipeline._stop_resolution_entry.attempt_stop_resolution",
            side_effect=mock_attempt_resolution_site1,
        ),
        patch("ralph.pipeline.auto_integrate_rebase_merge.set_resolving_rebase", return_value=True),
        patch("ralph.pipeline.conflict_resolution.status.conflict_status_bar_session"),
    ):
        mock_resolver = MagicMock()
        resolve_conflicted_rebase(
            root=tmp_path,
            target="main",
            rebase_stop_resolver=mock_resolver,
            display=None,
            conflict_resolution_config=None,
        )

    assert calls_site1 == ["finish_resolved_rebase_stops", "attempt_stop_resolution"]

    calls_site2: list[str] = []
    site2_root = tmp_path / "planning-entry"
    site2_root.mkdir()

    def mock_finish_stops_site2(root: Path, target: str) -> bool:
        calls_site2.append("finish_resolved_rebase_stops")
        return False

    def mock_attempt_resolution_site2(name: str, trigger: object) -> object:
        calls_site2.append("attempt_stop_resolution")
        return RetryLaterOutcome(on_change="paused")

    with (
        patch(
            "ralph.pipeline.conflict_resolution.rebase_loop.finish_resolved_rebase_stops",
            side_effect=mock_finish_stops_site2,
        ),
        patch(
            "ralph.pipeline._stop_resolution_entry.attempt_stop_resolution",
            side_effect=mock_attempt_resolution_site2,
        ),
        patch("ralph.pipeline.auto_integrate_planning.rebase_in_progress", return_value=True),
        patch(
            "ralph.pipeline.auto_integrate_planning.resolve_integration_target", return_value="main"
        ),
        patch("ralph.git.merge.observe_branch_sha", return_value=("feat_sha", True)),
        patch(
            "ralph.pipeline.conflict_resolution.rebase_loop.current_rebase_identity",
            return_value=("feat_sha", "onto_sha"),
        ),
    ):
        integrate_before_planning(
            config=MagicMock(),
            scope=MagicMock(root=site2_root),
            state=MagicMock(),
            rebase_stop_resolver=MagicMock(),
        )

    assert calls_site2 == ["finish_resolved_rebase_stops", "attempt_stop_resolution"]


def test_integrate_before_planning_retained_integration_reason_forces_retry(
    tmp_path: Path,
) -> None:
    expected_state = MagicMock(recovery_record_retained=True, fast_forwarded=False)
    with (
        patch(
            "ralph.pipeline.auto_integrate_planning.retained_integration_reason",
            return_value="observed_retained_on_disk",
        ),
        patch(
            "ralph.pipeline.auto_integrate_planning.recover_incomplete_integration",
            return_value=expected_state,
        ) as mock_recover,
    ):
        mock_scope = MagicMock(root=tmp_path)
        mock_config = MagicMock()
        mock_config.general.auto_integrate_enabled = True
        mock_state = MagicMock()
        mock_conflict_resolver = MagicMock()
        mock_stop_resolver = MagicMock()

        result = integrate_before_planning(
            mock_config,
            mock_scope,
            mock_state,
            conflict_resolver=mock_conflict_resolver,
            rebase_stop_resolver=mock_stop_resolver,
        )

        assert result is expected_state
        mock_recover.assert_called_once_with(
            mock_scope,
            config=mock_config,
            conflict_resolver=mock_conflict_resolver,
            rebase_stop_resolver=mock_stop_resolver,
            force_retry=True,
        )

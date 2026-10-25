"""Fallback-ladder executor tests: rungs 2-5 behind the decision vocabulary.

Pure fake-driven tests over the shared doubles in
:mod:`tests._integration_ladder_fakes`: the git plumbing, filesystem
quarantine and environment probe are scripted doubles recording every
mutation; the clock is the fake monotonic clock; the durable ledger is
the real :class:`~ralph.pipeline.integration_attempt_ledger.AttemptLedger`
on the per-test ``tmp_path`` (the same pattern
``tests/test_integration_attempt_ledger.py`` uses), so intent-before /
outcome-after recording and restart resume are proven against the real
persistence path. Rung 6 lives in ``tests/test_integration_ladder_last_resort.py``,
the production adapter argv contract in ``tests/test_integration_ladder_adapters.py``,
and the real-git regression in ``tests/test_integration_ladder_real_git.py``.

The ladder contract pinned here, rung by rung:

* intent is recorded BEFORE acting and the outcome AFTER, in the
  durable ledger, and survives a restart (a fresh ``AttemptLedger``
  reads the same evidence);
* every rung is idempotent and a rung observation shows cannot succeed
  is reported ``impossible`` and skipped, not repeated;
* rungs 2-5 never discard work: preserved copies (ref or sidecar) are
  recorded and never touched by a later rung;
* a failed probe is UNKNOWN, never clean: rung 2 reports
  ``failed:changed-paths-unreadable`` and the ladder escalates instead
  of reading "nothing to preserve";
* rung 4 lands with compare-and-swap semantics: a concurrent target
  advance names the race (``failed:land:target-moved-concurrently``)
  and overwrites nothing, and the original checkout is repointed only
  when it is still where the ladder observed it;
* a restart mid-ladder resumes at the recorded rung.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.pipeline.integration_actions import Clock
from ralph.pipeline.integration_decision import (
    CLEANUP_LADDER_ORDER,
    CleanupLadderRung,
    NextAction,
)
from ralph.pipeline.integration_ladder import (
    EnvironmentProbe,
    FileQuarantine,
    GitPlumbing,
    LadderStepResult,
    adapt_environment_and_retry,
    ladder_charge_count,
    preserve_via_temp_index,
    quarantine_and_restore,
    rebuild_in_fresh_worktree,
    record_salvage_attempt,
    resume_rung,
    run_ladder_rung,
)
from ralph.pipeline.integration_observation import InProgressKind
from ralph.pipeline.integration_owner import (
    IntegrationOwnerDecision,
    ladder_rung_for_action,
    project_integration_action,
)
from tests._fake_git_clock import FakeClock
from tests._integration_ladder_fakes import (
    _evidence,
    _FakeEnvProbe,
    _FakePlumbing,
    _FakeQuarantineFs,
    _ledger,
    _observe_into,
    _record,
)

pytestmark = pytest.mark.timeout_seconds(30)


# ---------------------------------------------------------------------------
# Rung 2 — alternate preservation.
# ---------------------------------------------------------------------------


def test_rung2_commits_dirt_to_a_verified_ref_and_survives_restart(
    tmp_path: Path,
) -> None:
    """The plumbing commit preserves the dirt; evidence is durable."""
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()
    fs = _FakeQuarantineFs()

    result = preserve_via_temp_index(
        tmp_path, git=git, fs=fs, ledger=ledger, state_dir=tmp_path / ".agent"
    )

    assert result.outcome == "succeeded"
    assert result.preserved_ref == "refs/ralph-backup/fake"
    assert result.preserved_sidecar is None
    assert "commit_dirt" in git.calls
    assert "ref_is_readable:refs/ralph-backup/fake" in git.calls
    assert _evidence(tmp_path)["rung:preserve_via_temp_index"] == (
        "succeeded:ref=refs/ralph-backup/fake"
    )
    assert _evidence(tmp_path)["preserved:alternate_preservation"] == "refs/ralph-backup/fake"


def test_rung2_ref_failure_prunes_and_writes_verified_sidecar(tmp_path: Path) -> None:
    """No ref: free space (artifacts + verified-landed refs), then sidecar."""
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(commit_ref=None, readable_refs=frozenset())
    # The plumbing records what it wrote; the fs verifies readability of
    # exactly those paths — one shared set, no monkey-patching.
    fs = _FakeQuarantineFs(existing=git.written)
    result = preserve_via_temp_index(
        tmp_path,
        git=git,
        fs=fs,
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        is_landed=lambda _ref: True,
    )

    assert result.outcome == "succeeded"
    assert result.preserved_sidecar is not None
    assert result.preserved_ref is None
    assert "prune_landed_backup_refs" in git.calls
    assert _evidence(tmp_path)["rung:preserve_via_temp_index"].startswith("succeeded:sidecar=")


def test_rung2_no_verified_copy_fails_without_discarding(tmp_path: Path) -> None:
    """Both preservation routes failing is a typed failure, never a silent loss."""
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(commit_ref=None, readable_refs=frozenset(), fail_patch=True)
    fs = _FakeQuarantineFs()

    result = preserve_via_temp_index(
        tmp_path, git=git, fs=fs, ledger=ledger, state_dir=tmp_path / ".agent"
    )

    assert result.outcome == "failed"
    assert _evidence(tmp_path)["rung:preserve_via_temp_index"] == "failed:no-durable-copy"


def test_rung2_with_no_dirt_succeeds_immediately(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(changed=())

    result = preserve_via_temp_index(
        tmp_path, git=git, fs=_FakeQuarantineFs(), ledger=ledger, state_dir=tmp_path
    )

    assert result.outcome == "succeeded"
    assert "commit_dirt" not in git.calls


def test_rung2_unreadable_probe_fails_closed_never_nothing_to_preserve(tmp_path: Path) -> None:
    """A failed probe is UNKNOWN, never clean: the rung fails and the ladder escalates."""
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(changed_unreadable=True)

    result = preserve_via_temp_index(
        tmp_path, git=git, fs=_FakeQuarantineFs(), ledger=ledger, state_dir=tmp_path / ".agent"
    )

    assert result.outcome == "failed"
    assert result.reason == "failed:changed-paths-unreadable"
    assert "commit_dirt" not in git.calls
    assert not any(call.startswith("write_patch") for call in git.calls)
    assert _evidence(tmp_path)["rung:preserve_via_temp_index"] == "failed:changed-paths-unreadable"


# ---------------------------------------------------------------------------
# Rung 3 — quarantine.
# ---------------------------------------------------------------------------


def test_rung3_renames_and_restores_baseline(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()
    fs = _FakeQuarantineFs()

    result = quarantine_and_restore(
        tmp_path,
        paths=("src/locked.py", "dir-for-file"),
        git=git,
        fs=fs,
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
    )

    assert result.outcome == "succeeded"
    assert result.quarantined_paths == (
        f"{tmp_path}/.agent/quarantine/tick-1000/src/locked.py",
        f"{tmp_path}/.agent/quarantine/tick-1000/dir-for-file",
    )
    assert "restore_tree_to_head" in git.calls
    assert _evidence(tmp_path)["rung:quarantine_and_restore"].startswith("succeeded:")


def test_rung3_impossible_rename_skips_to_rung4_not_repeated(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()
    fs = _FakeQuarantineFs(rename_fails=frozenset({"dir-for-file"}))

    result = quarantine_and_restore(
        tmp_path,
        paths=("src/locked.py", "dir-for-file"),
        git=git,
        fs=fs,
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
    )

    assert result.outcome == "impossible"
    assert result.next_rung is CleanupLadderRung.FRESH_WORKTREE
    assert "restore_tree_to_head" not in git.calls
    assert _evidence(tmp_path)["rung:quarantine_and_restore"].startswith("impossible:")


def test_rung3_restore_failure_is_a_typed_failure(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(fail_restore=True)

    result = quarantine_and_restore(
        tmp_path,
        paths=("src/locked.py",),
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
    )

    assert result.outcome == "failed"
    assert _evidence(tmp_path)["rung:quarantine_and_restore"] == "failed:restore-tree-to-head"


# ---------------------------------------------------------------------------
# Rung 4 — fresh isolated worktree.
# ---------------------------------------------------------------------------


def test_rung4_rebuilds_lands_and_records_salvage(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()

    result = rebuild_in_fresh_worktree(
        tmp_path,
        target="main",
        branch="feature",
        git=git,
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        preserved_ref="refs/ralph-backup/fake",
        expected_target_sha="cafe01",
    )

    assert result.outcome == "succeeded"
    assert "apply_preserved:refs/ralph-backup/fake" in git.calls
    # The expected sha observed at rung start is what the CAS is checked against.
    assert "land_in_worktree:main:cafe01" in git.calls
    assert "repoint_original:abc123" in git.calls
    assert _evidence(tmp_path)["salvage:fresh_worktree_rebuild"] == "succeeded"


def test_rung4_probes_expected_target_sha_when_the_caller_passes_none(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(target_shas={"main": "beef42"})

    result = rebuild_in_fresh_worktree(
        tmp_path,
        target="main",
        branch="feature",
        git=git,
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
    )

    assert result.outcome == "succeeded"
    assert "target_sha:main" in git.calls
    assert "land_in_worktree:main:beef42" in git.calls


def test_rung4_unreadable_target_sha_fails_closed_before_any_mutation(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(target_shas={})

    result = rebuild_in_fresh_worktree(
        tmp_path,
        target="main",
        branch="feature",
        git=git,
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
    )

    assert result.outcome == "failed"
    assert result.reason == "failed:land:target-sha-unreadable"
    assert "create_worktree:main" not in git.calls
    assert not any(call.startswith("land_in_worktree") for call in git.calls)


def test_rung4_land_race_is_typed_names_the_race_and_overwrites_nothing(tmp_path: Path) -> None:
    """A concurrent target advance: no repoint, no overwrite; the caller re-observes."""
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(land_result="target-moved")

    result = rebuild_in_fresh_worktree(
        tmp_path,
        target="main",
        branch="feature",
        git=git,
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        expected_target_sha="cafe01",
    )

    assert result.outcome == "failed"
    assert result.reason == "failed:land:target-moved-concurrently"
    assert "repoint_original" not in git.calls
    assert _evidence(tmp_path)["rung:rebuild_in_fresh_worktree"] == (
        "failed:land:target-moved-concurrently"
    )


def test_rung4_repoint_not_now_defers_without_failing_the_landed_rung(tmp_path: Path) -> None:
    """The target landed; the original checkout is owned elsewhere: defer, don't reset."""
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(repoint_result="head-moved")

    result = rebuild_in_fresh_worktree(
        tmp_path,
        target="main",
        branch="feature",
        git=git,
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        expected_target_sha="cafe01",
    )

    assert result.outcome == "succeeded"
    assert "repoint-not-now:head-moved" in result.reason
    assert _evidence(tmp_path)["salvage:fresh_worktree_rebuild"] == "succeeded"


def test_rung4_repoint_reset_failure_is_a_typed_failure(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(repoint_result="reset-failed")

    result = rebuild_in_fresh_worktree(
        tmp_path,
        target="main",
        branch="feature",
        git=git,
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        expected_target_sha="cafe01",
    )

    assert result.outcome == "failed"
    assert result.reason == "failed:repoint-original"


def test_rung4_step_failure_is_typed_and_stops_the_sequence(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(land_result="update-failed")

    result = rebuild_in_fresh_worktree(
        tmp_path,
        target="main",
        branch="feature",
        git=git,
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
    )

    assert result.outcome == "failed"
    assert result.reason == "failed:land:update-failed"
    assert "repoint_original" not in git.calls


# ---------------------------------------------------------------------------
# Rung 5 — degrade, don't die.
# ---------------------------------------------------------------------------


def test_rung5_adapts_backs_off_on_the_fake_clock_and_reobserves(tmp_path: Path) -> None:
    clock = FakeClock(initial_time=500.0)
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()
    fs = _FakeQuarantineFs(alternative=tmp_path / "alt-state")
    observed: list[str] = []
    reminders: list[str] = []

    first = adapt_environment_and_retry(
        tmp_path,
        git=git,
        fs=fs,
        env=_FakeEnvProbe("read_only_fs"),
        ledger=ledger,
        clock=clock,
        state_dir=tmp_path / ".agent",
        observe=lambda: _observe_into(observed, "fresh-sig"),
        remind=reminders.append,
    )
    second = adapt_environment_and_retry(
        tmp_path,
        git=git,
        fs=fs,
        env=_FakeEnvProbe("read_only_fs"),
        ledger=ledger,
        clock=clock,
        state_dir=tmp_path / ".agent",
        observe=lambda: "fresh-sig",
        remind=reminders.append,
    )

    assert first.outcome == "waiting"
    assert second.outcome == "waiting"
    # Capped exponential backoff on the injectable clock: 1s, then 2s.
    assert clock.now() == 503.0
    assert observed == ["fresh-sig"]
    assert "n=1:" in first.reason and "n=2:" in second.reason
    # Adaptation happened, and the reminder is rate-limited to one line.
    assert "prune_regenerable_artifacts" in fs.calls
    assert "alternative_state_dir" in fs.calls
    assert len(reminders) == 1


def test_rung5_safe_directory_sets_config_for_the_owned_path(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()

    result = adapt_environment_and_retry(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        env=_FakeEnvProbe("safe_directory"),
        ledger=ledger,
        clock=FakeClock(),
        state_dir=tmp_path / ".agent",
    )

    assert result.outcome == "waiting"
    assert "set_safe_directory" in git.calls
    assert "failure=safe_directory" in result.reason


def test_rung5_healthy_environment_still_reobserves_without_adapting(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()
    fs = _FakeQuarantineFs()

    result = adapt_environment_and_retry(
        tmp_path,
        git=git,
        fs=fs,
        env=_FakeEnvProbe(None),
        ledger=ledger,
        clock=FakeClock(),
        state_dir=tmp_path / ".agent",
    )

    assert result.outcome == "waiting"
    assert "set_safe_directory" not in git.calls
    assert "prune_regenerable_artifacts" not in fs.calls


# ---------------------------------------------------------------------------
# Restart resume, budget, dispatcher and owner routing.
# ---------------------------------------------------------------------------


def test_restart_resumes_at_the_recorded_rung_without_repeating(tmp_path: Path) -> None:
    """A restart never repeats finished rungs and never loses preserved copies."""
    ledger = _ledger(tmp_path)
    _record(ledger, "rung:save_and_restore", "succeeded:ref=refs/ralph-backup/one")
    _record(ledger, "rung:preserve_via_temp_index", "intent")
    _record(ledger, "preserved:alternate_preservation", "refs/ralph-backup/one")

    state = _ledger(tmp_path).read()
    assert resume_rung(state) is CleanupLadderRung.ALTERNATE_PRESERVATION
    assert state.ladder_evidence["preserved:alternate_preservation"] == ("refs/ralph-backup/one")

    _record(ledger, "rung:preserve_via_temp_index", "succeeded:ref=refs/ralph-backup/two")
    _record(ledger, "rung:quarantine_and_restore", "impossible:rename-failed:x")
    _record(ledger, "rung:rebuild_in_fresh_worktree", "failed:land")
    # A completed waiting tick is a finished rung: the restart resumes
    # past it and the decision table re-decides from the budget.
    _record(ledger, "rung:adapt_environment_and_retry", "waiting:n=1:failure=none:delay=1.0")
    assert resume_rung(_ledger(tmp_path).read()) is CleanupLadderRung.LAST_RESORT
    # A rung left at intent (crash mid-rung) resumes there, idempotently.
    _record(ledger, "rung:adapt_environment_and_retry", "intent")
    assert resume_rung(_ledger(tmp_path).read()) is CleanupLadderRung.ENVIRONMENT_DEGRADE
    _record(ledger, "rung:adapt_environment_and_retry", "failed:still-broken")
    assert resume_rung(_ledger(tmp_path).read()) is CleanupLadderRung.LAST_RESORT
    _record(ledger, "rung:last_resort_recovery", "succeeded:reset-hard-to=main")
    assert resume_rung(_ledger(tmp_path).read()) is None


def test_resume_rung_starts_at_the_bottom_on_a_fresh_ledger(tmp_path: Path) -> None:
    assert resume_rung(_ledger(tmp_path).read()) is CleanupLadderRung.NORMAL_CLEANUP
    assert set(CLEANUP_LADDER_ORDER) == set(CleanupLadderRung)


def test_ladder_budget_counts_only_cleanup_ladder_charges(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    ledger.charge("sig", NextAction.SAVE_AND_RESTORE.value, is_unknown=False)
    ledger.charge("sig", NextAction.LAND.value, is_unknown=False)
    ledger.charge("sig", NextAction.PRESERVE_VIA_TEMP_INDEX.value, is_unknown=False)
    assert ladder_charge_count(ledger.read()) == 2


def test_record_salvage_attempt_rejects_unknown_kinds(tmp_path: Path) -> None:
    with pytest.raises(ValueError, match="unknown salvage kind"):
        record_salvage_attempt(_ledger(tmp_path), "diagnostic", "failed:x")


def test_run_ladder_rung_dispatches_quarantine_to_the_executor(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()
    result = run_ladder_rung(
        CleanupLadderRung.QUARANTINE,
        root=tmp_path,
        ledger=ledger,
        git=git,
        fs=_FakeQuarantineFs(),
        clock=FakeClock(),
        state_dir=tmp_path / ".agent",
        paths=("src/locked.py",),
    )
    assert result.rung is CleanupLadderRung.QUARANTINE
    assert result.outcome == "succeeded"
    assert "restore_tree_to_head" in git.calls


def test_run_ladder_rung_rung1_is_not_owned_here(tmp_path: Path) -> None:
    """Rung 1 (normal cleanup) is another unit's executor; the ladder refuses."""
    result = run_ladder_rung(
        CleanupLadderRung.NORMAL_CLEANUP,
        root=tmp_path,
        ledger=_ledger(tmp_path),
        git=_FakePlumbing(),
        fs=_FakeQuarantineFs(),
        clock=FakeClock(),
        state_dir=tmp_path / ".agent",
    )
    assert result.outcome == "failed"
    assert result.reason == "failed:not-owned-here"


def test_owner_projection_routes_every_ladder_action_to_its_rung() -> None:
    """The owner's projection layer maps the rung actions to executors."""
    expected = {
        NextAction.SAVE_AND_RESTORE: CleanupLadderRung.NORMAL_CLEANUP,
        NextAction.PRESERVE_VIA_TEMP_INDEX: CleanupLadderRung.ALTERNATE_PRESERVATION,
        NextAction.QUARANTINE_AND_RESTORE: CleanupLadderRung.QUARANTINE,
        NextAction.REBUILD_IN_FRESH_WORKTREE: CleanupLadderRung.FRESH_WORKTREE,
        NextAction.ADAPT_ENVIRONMENT_AND_RETRY: CleanupLadderRung.ENVIRONMENT_DEGRADE,
        NextAction.LAST_RESORT_RECOVERY: CleanupLadderRung.LAST_RESORT,
    }
    for action, rung in expected.items():
        assert ladder_rung_for_action(action) is rung
    non_ladder = set(NextAction) - set(expected)
    assert non_ladder
    for action in non_ladder:
        assert ladder_rung_for_action(action) is None


def test_owner_verdict_carries_the_rung_field_and_defaults_to_none(tmp_path: Path) -> None:
    """Non-ladder verdicts default to no rung; the field is part of the verdict."""
    from ralph.pipeline.integration_observation import (
        HeadState,
        InProgressOwnership,
        IntegrationRecordState,
        Observation,
        TargetVsFeature,
        TargetVsRemote,
        WorktreeState,
    )

    observation = Observation(
        target_vs_feature=TargetVsFeature.EQUAL,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.CLEAN,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    )
    verdict = IntegrationOwnerDecision(
        action=project_integration_action(NextAction.LAND),
        reason="clean",
        observation=observation,
        attempts=_ledger(tmp_path).read(),
        escalation_charged=True,
    )
    assert verdict.ladder_rung is None
    routed = IntegrationOwnerDecision(
        action=verdict.action,
        reason=verdict.reason,
        observation=observation,
        attempts=verdict.attempts,
        escalation_charged=True,
        ladder_rung=CleanupLadderRung.QUARANTINE,
    )
    assert routed.ladder_rung is CleanupLadderRung.QUARANTINE


def test_ladder_step_result_defaults() -> None:
    result = LadderStepResult(CleanupLadderRung.QUARANTINE, "failed", "failed:restore-tree-to-head")
    assert result.preserved_ref is None
    assert result.preserved_sidecar is None
    assert result.quarantined_paths == ()
    assert result.next_rung is None
    assert result.evidence == {}


def test_seams_are_runtime_checkable_protocols() -> None:
    """The fakes genuinely implement the public protocol aliases."""
    assert isinstance(_FakePlumbing(), GitPlumbing)
    assert isinstance(_FakeQuarantineFs(), FileQuarantine)
    assert isinstance(_FakeEnvProbe("disk_full"), EnvironmentProbe)
    assert isinstance(FakeClock(), Clock)

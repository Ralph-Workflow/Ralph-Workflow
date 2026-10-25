"""Fallback-ladder executor tests: rungs 2-6 behind the decision vocabulary.

Pure fake-driven tests: no real Git, no real sleeps, no subprocess. The
git plumbing, filesystem quarantine and environment probe are scripted
doubles recording every mutation; the clock is the fake monotonic
clock; the durable ledger is the real
:class:`~ralph.pipeline.integration_attempt_ledger.AttemptLedger` on
the per-test ``tmp_path`` (the same pattern
``tests/test_integration_attempt_ledger.py`` uses), so intent-before /
outcome-after recording and restart resume are proven against the real
persistence path.

The ladder contract pinned here, rung by rung:

* intent is recorded BEFORE acting and the outcome AFTER, in the
  durable ledger, and survives a restart (a fresh ``AttemptLedger``
  reads the same evidence);
* every rung is idempotent and a rung observation shows cannot succeed
  is reported ``impossible`` and skipped, not repeated;
* rungs 2-5 never discard work: preserved copies (ref or sidecar) are
  recorded and never touched by a later rung;
* the last-resort reset runs ONLY when all six preconditions hold,
  preserves first, resets to the feature tip (or the target when the
  tip is unusable), and never runs early or because an attempt merely
  failed once;
* a restart mid-ladder resumes at the recorded rung.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.pipeline.integration_actions import Clock
from ralph.pipeline.integration_attempt_ledger import AttemptLedger, AttemptLedgerState
from ralph.pipeline.integration_decision import (
    CLEANUP_LADDER_ORDER,
    CleanupLadderRung,
    NextAction,
)
from ralph.pipeline.integration_ladder import (
    LADDER_ATTEMPT_BUDGET,
    LAST_RESORT_PRECONDITIONS,
    EnvironmentFailure,
    EnvironmentProbe,
    FileQuarantine,
    GitPlumbing,
    LadderStepResult,
    adapt_environment_and_retry,
    evaluate_last_resort_preconditions,
    ladder_charge_count,
    last_resort_recovery,
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

if TYPE_CHECKING:
    from collections.abc import Callable

pytestmark = pytest.mark.timeout_seconds(30)


# ---------------------------------------------------------------------------
# Fakes.
# ---------------------------------------------------------------------------


class _FakePlumbing:
    """Scripted :class:`GitPlumbing` double recording every mutation."""

    def __init__(
        self,
        *,
        changed: tuple[str, ...] = ("src/a.py",),
        head: str | None = "abc123",
        commit_ref: str | None = "refs/ralph-backup/fake",
        readable_refs: frozenset[str] = frozenset({"refs/ralph-backup/fake"}),
        rename_blocked: frozenset[str] = frozenset(),
        pruned_landed: tuple[str, ...] = ("refs/ralph-backup/landed",),
        fail_patch: bool = False,
        fail_tar: bool = False,
        fail_restore: bool = False,
        fail_abort: bool = False,
        fail_first_reset: bool = False,
        fail_reset_forever: bool = False,
        fail_recreate: bool = False,
        fail_create_worktree: bool = False,
        fail_apply: bool = False,
        fail_land: bool = False,
        fail_repoint: bool = False,
        fail_safe_directory: bool = False,
    ) -> None:
        self._changed = changed
        self._head = head
        self._commit_ref = commit_ref
        self._readable_refs = readable_refs
        self.rename_blocked = rename_blocked
        self._pruned_landed = pruned_landed
        self.fail_patch = fail_patch
        self.fail_tar = fail_tar
        self.fail_restore = fail_restore
        self.fail_abort = fail_abort
        self.fail_first_reset = fail_first_reset
        self.fail_reset_count = 0
        self.fail_reset_forever = fail_reset_forever
        self.fail_recreate = fail_recreate
        self.fail_create_worktree = fail_create_worktree
        self.fail_apply = fail_apply
        self.fail_land = fail_land
        self.fail_repoint = fail_repoint
        self.fail_safe_directory = fail_safe_directory
        self.written: set[Path] = set()
        self.calls: list[str] = []

    def changed_paths(self, root: Path) -> tuple[str, ...]:
        self.calls.append("changed_paths")
        return self._changed

    def head_sha(self, root: Path) -> str | None:
        self.calls.append("head_sha")
        return self._head

    def commit_dirt(self, root: Path, head_sha: str, owning_worktree: str | None) -> str | None:
        self.calls.append("commit_dirt")
        return self._commit_ref

    def ref_is_readable(self, root: Path, ref: str) -> bool:
        self.calls.append(f"ref_is_readable:{ref}")
        return ref in self._readable_refs

    def prune_landed_backup_refs(
        self, root: Path, is_landed: Callable[[str], bool]
    ) -> tuple[str, ...]:
        self.calls.append("prune_landed_backup_refs")
        return tuple(ref for ref in self._pruned_landed if is_landed(ref))

    def write_patch(self, root: Path, paths: tuple[str, ...], dest: Path) -> bool:
        self.calls.append(f"write_patch:{dest.name}")
        if not self.fail_patch:
            self.written.add(dest)
        return not self.fail_patch

    def archive_paths(self, root: Path, paths: tuple[str, ...], dest: Path) -> bool:
        self.calls.append(f"archive_paths:{dest.name}")
        if not self.fail_tar:
            self.written.add(dest)
        return not self.fail_tar

    def restore_tree_to_head(self, root: Path) -> bool:
        self.calls.append("restore_tree_to_head")
        return not self.fail_restore

    def abort_operation(self, root: Path, kind: InProgressKind) -> bool:
        self.calls.append(f"abort_operation:{kind.value}")
        return not self.fail_abort

    def remove_operation_state(self, root: Path, kind: InProgressKind) -> bool:
        self.calls.append(f"remove_operation_state:{kind.value}")
        return True

    def reset_hard(self, root: Path, revision: str) -> bool:
        self.calls.append(f"reset_hard:{revision}")
        self.fail_reset_count += 1
        failed = self.fail_reset_forever or (self.fail_first_reset and self.fail_reset_count == 1)
        return not failed

    def clean_untracked(self, root: Path, paths: tuple[str, ...]) -> bool:
        self.calls.append("clean_untracked")
        return True

    def remove_stale_locks(self, root: Path) -> tuple[str, ...]:
        self.calls.append("remove_stale_locks")
        return ("index.lock",)

    def recreate_worktree(self, root: Path, branch: str) -> bool:
        self.calls.append(f"recreate_worktree:{branch}")
        return not self.fail_recreate

    def set_safe_directory(self, root: Path) -> bool:
        self.calls.append("set_safe_directory")
        return not self.fail_safe_directory

    def repair_gitdir(self, root: Path) -> bool:
        self.calls.append("repair_gitdir")
        return True

    def create_worktree(self, root: Path, path: Path, target: str) -> bool:
        self.calls.append(f"create_worktree:{target}")
        return not self.fail_create_worktree

    def apply_preserved(self, root: Path, path: Path, preserved_ref: str) -> bool:
        self.calls.append(f"apply_preserved:{preserved_ref}")
        return not self.fail_apply

    def land_in_worktree(self, root: Path, path: Path, target: str) -> bool:
        self.calls.append(f"land_in_worktree:{target}")
        return not self.fail_land

    def repoint_original(self, root: Path, path: Path) -> bool:
        self.calls.append("repoint_original")
        return not self.fail_repoint

    def remote_reachable(self, root: Path, remote: str) -> bool:
        self.calls.append(f"remote_reachable:{remote}")
        return True


class _FakeQuarantineFs:
    """Scripted :class:`FileQuarantine` double over an in-memory map."""

    def __init__(
        self,
        *,
        existing: set[Path] | None = None,
        rename_fails: frozenset[str] = frozenset(),
        pruned: tuple[str, ...] = ("tmp", "stale.staging"),
        alternative: Path | None = None,
    ) -> None:
        self._existing = existing if existing is not None else set()
        self._rename_fails = rename_fails
        self._pruned = pruned
        self._alternative = alternative
        self.renamed: list[tuple[str, str]] = []
        self.calls: list[str] = []

    def rename_into_quarantine(self, root: Path, quarantine_dir: Path, path: str) -> str | None:
        self.calls.append(f"rename:{path}")
        if path in self._rename_fails:
            return None
        dest = str(quarantine_dir / path)
        self.renamed.append((path, dest))
        return dest

    def prune_regenerable_artifacts(self, state_dir: Path) -> tuple[str, ...]:
        self.calls.append("prune_regenerable_artifacts")
        return self._pruned

    def path_is_readable(self, path: Path) -> bool:
        self.calls.append(f"readable:{path.name}")
        return path in self._existing

    def alternative_state_dir(self, root: Path) -> Path | None:
        self.calls.append("alternative_state_dir")
        return self._alternative


class _FakeEnvProbe:
    """Scripted :class:`EnvironmentProbe` double."""

    def __init__(self, failure: EnvironmentFailure | None = None) -> None:
        self._failure = failure

    def failure_class(self, root: Path, *, remote: str) -> EnvironmentFailure | None:
        return self._failure


# ---------------------------------------------------------------------------
# Helpers.
# ---------------------------------------------------------------------------


def _ledger(tmp_path: Path) -> AttemptLedger:
    return AttemptLedger(tmp_path)


def _evidence(tmp_path: Path) -> dict[str, str]:
    return _ledger(tmp_path).read().ladder_evidence


def _record(ledger: AttemptLedger, key: str, value: str) -> None:
    state = ledger.read()
    merged = AttemptLedgerState(
        rung=state.rung,
        repeat_counts=state.repeat_counts,
        unknown_attempts=state.unknown_attempts,
        unknown_budget=state.unknown_budget,
        repeat_threshold=state.repeat_threshold,
        saturated_repeats=state.saturated_repeats,
        global_actions_charged=state.global_actions_charged,
        ladder_evidence={**state.ladder_evidence, key: value},
    )
    ledger.persist(merged)


def _seed_terminal_rungs(ledger: AttemptLedger) -> None:
    """Record every rung 1-5 as tried-and-failed, as the ladder would."""
    outcomes = {
        "save_and_restore": "failed:restore-refused",
        "preserve_via_temp_index": "failed:no-durable-copy",
        "quarantine_and_restore": "impossible:rename-failed:src/locked.py",
        "rebuild_in_fresh_worktree": "failed:land",
        "adapt_environment_and_retry": "waiting:n=6:failure=read_only_fs:delay=60.0",
    }
    for action, outcome in outcomes.items():
        _record(ledger, f"rung:{action}", outcome)


def _seed_salvage(ledger: AttemptLedger) -> None:
    record_salvage_attempt(ledger, "resolver_agent", "failed:no-progress")
    record_salvage_attempt(ledger, "rebase_first_retry", "failed:same-signature")
    record_salvage_attempt(ledger, "fresh_worktree_rebuild", "failed:land")


def _seed_budget(ledger: AttemptLedger, signature: str = "sig") -> None:
    """Charge the whole-ladder budget to the durable ledger."""
    while ladder_charge_count(ledger.read()) < LADDER_ATTEMPT_BUDGET:
        ledger.charge(signature, NextAction.SAVE_AND_RESTORE.value, is_unknown=False)


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
    )

    assert result.outcome == "succeeded"
    assert "apply_preserved:refs/ralph-backup/fake" in git.calls
    assert "land_in_worktree:main" in git.calls
    assert "repoint_original" in git.calls
    assert _evidence(tmp_path)["salvage:fresh_worktree_rebuild"] == "succeeded"


def test_rung4_step_failure_is_typed_and_stops_the_sequence(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    git = _FakePlumbing(fail_land=True)

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
    assert result.reason == "failed:land"
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
# Rung 6 — last resort.
# ---------------------------------------------------------------------------

_SIG = "behind|tracked_changes|none|none|attached"


def _observe_into(sink: list[str], signature: str) -> str:
    """Record the re-observation and return it (a statement, not a value trick)."""
    sink.append(signature)
    return signature


def _seed_all_preconditions(ledger: AttemptLedger) -> None:
    _seed_terminal_rungs(ledger)
    _seed_salvage(ledger)
    _seed_budget(ledger)


def test_rung6_never_resets_when_any_precondition_is_unmet(tmp_path: Path) -> None:
    """An empty ledger means no rung was ever tried: the reset MUST NOT run."""
    ledger = _ledger(tmp_path)
    git = _FakePlumbing()

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature=_SIG,
    )

    assert result.outcome == "failed"
    assert result.reason.startswith("failed:precondition-unmet:")
    assert not any(call.startswith("reset_hard") for call in git.calls)
    # Every precondition's evidence is recorded all the same; the
    # re-observation one IS met here (the signatures match) — the reset
    # is still refused by the five unmet rung/salvage/budget ones.
    evidence = _evidence(tmp_path)
    for name in LAST_RESORT_PRECONDITIONS:
        if name == "fresh_reobservation_confirms_blocking_state":
            assert evidence[f"precondition:{name}"].startswith("met")
        else:
            assert evidence[f"precondition:{name}"].startswith("unmet")


def test_rung6_healed_state_cancels_the_reset(tmp_path: Path) -> None:
    """A fresh re-observation that no longer matches cancels the reset."""
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing()

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature="clean|clean|none|none|attached",
    )

    assert result.outcome == "failed"
    assert result.reason == "failed:precondition-unmet:fresh_reobservation_confirms_blocking_state"
    assert not any(call.startswith("reset_hard") for call in git.calls)


def test_rung6_resets_only_after_all_six_preconditions_hold(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing(readable_refs=frozenset({"tipsha"}))

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature=_SIG,
        untracked_paths=("scratch.txt",),
        in_progress_kind=InProgressKind.REBASE,
    )

    assert result.outcome == "succeeded"
    assert "abort_operation:rebase" in git.calls
    assert "reset_hard:tipsha" in git.calls
    assert "clean_untracked" in git.calls
    assert "remove_stale_locks" in git.calls
    # Preserved copies are never pruned by the reset.
    assert "prune_landed_backup_refs" not in git.calls
    evidence = _evidence(tmp_path)
    for name in LAST_RESORT_PRECONDITIONS:
        assert evidence[f"precondition:{name}"].startswith("met"), name


def test_rung6_runs_preservation_immediately_before_the_reset(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing(readable_refs=frozenset({"tipsha", "refs/ralph-backup/fake"}))

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature=_SIG,
        paths=("src/a.py",),
    )

    assert result.outcome == "succeeded"
    order = [call for call in git.calls if call in ("commit_dirt", "reset_hard:tipsha")]
    assert order == ["commit_dirt", "reset_hard:tipsha"]
    assert _evidence(tmp_path)["preserved:alternate_preservation"] == "refs/ralph-backup/fake"


def test_rung6_resets_to_target_when_the_feature_tip_is_unusable(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing(readable_refs=frozenset())

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature=_SIG,
    )

    assert result.outcome == "succeeded"
    assert "reset_hard:main" in git.calls


def test_rung6_recreates_the_worktree_when_even_the_reset_fails(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing(readable_refs=frozenset({"tipsha"}), fail_first_reset=True)

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature=_SIG,
    )

    assert result.outcome == "succeeded"
    assert "recreate_worktree:feature" in git.calls
    assert git.calls.count("reset_hard:tipsha") == 2


def test_rung6_abort_failure_deletes_operation_state(tmp_path: Path) -> None:
    ledger = _ledger(tmp_path)
    _seed_all_preconditions(ledger)
    git = _FakePlumbing(readable_refs=frozenset({"tipsha"}), fail_abort=True)

    result = last_resort_recovery(
        tmp_path,
        git=git,
        fs=_FakeQuarantineFs(),
        ledger=ledger,
        state_dir=tmp_path / ".agent",
        clock=FakeClock(),
        feature_tip="tipsha",
        target="main",
        branch="feature",
        decided_signature=_SIG,
        fresh_signature=_SIG,
        in_progress_kind=InProgressKind.MERGE,
    )

    assert result.outcome == "succeeded"
    assert "remove_operation_state:merge" in git.calls


def test_evaluate_preconditions_reports_every_one() -> None:
    state = AttemptLedgerState(ladder_evidence={"rung:save_and_restore": "succeeded:x"})
    results = evaluate_last_resort_preconditions(
        state, decided_signature=_SIG, fresh_signature=_SIG
    )
    assert tuple(results) == LAST_RESORT_PRECONDITIONS
    assert results["rungs_1_to_5_tried_or_shown_impossible"].startswith("unmet")
    assert results["ladder_attempt_budget_spent"].startswith("unmet")
    assert results["fresh_reobservation_confirms_blocking_state"].startswith("met")


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

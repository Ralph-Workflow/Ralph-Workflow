"""Planning-residue cleanup executor tests (AC-10 machinery).

Everything is pure in-memory: the git effects run through a fake
:class:`CleanupGitPort` (no subprocess, no filesystem, no clock) and the
ledger through a fake :class:`ResidueLedgerPort`. This is the
``audit_test_policy`` contract: only the decision-table projection checks
touch the real pure table.
"""

from __future__ import annotations

import hashlib
import json
from collections import OrderedDict
from collections.abc import Mapping, Sequence
from pathlib import Path
from typing import Any

import pytest

from ralph.pipeline.integration_cleanup import (
    OwnerResidueLedgerPort,
    PlanningBaseline,
    PlanningResidue,
    ResidueCleanupOutcome,
    capture_planning_baseline,
    compute_fingerprint,
    detect_planning_residue,
    residue_attribution,
    residue_observation,
    restore_quarantined_untracked,
    run_residue_cleanup,
)
from ralph.pipeline.integration_decision import (
    AttemptState,
    DecisionContext,
    DirtAttribution,
    NextAction,
    decide,
)
from ralph.pipeline.integration_observation import (
    DirtShape,
    InProgressKind,
    Reading,
)

pytestmark = pytest.mark.timeout_seconds(30)

_ROOT = Path("/fake/root")

_MISSING = "<missing>"


def _diff_hash(worktree: Mapping[str, str], committed: Mapping[str, str]) -> str:
    dirty = {
        path: content
        for path, content in sorted(worktree.items())
        if committed.get(path, _MISSING) != content
    }
    return hashlib.sha256(json.dumps(dirty, sort_keys=True).encode()).hexdigest()


class _FakeCleanupGit:
    """In-memory CleanupGitPort: committed/worktree/staged/untracked maps."""

    def __init__(self) -> None:
        self.head = "head-sha-1"
        self.committed: dict[str, str] = {"src/app.py": "base"}
        self.worktree: dict[str, str] = {"src/app.py": "base"}
        self.staged: dict[str, str] = {}
        self.untracked: dict[str, str] = {}
        self.incoming: frozenset[str] = frozenset()
        self.existing_refs: set[str] = set()
        self.snapshot_count = 0
        self.restore_calls: list[tuple[str, tuple[str, ...]]] = []
        self.preserve_calls: list[tuple[str, ...]] = []
        self.quarantine_calls: list[tuple[str, ...]] = []
        self.quarantined: dict[str, str] = {}
        self.state: dict[str, Any] | None = None
        self.fail_restore = False
        self.fail_reobserve = False
        self.forced_blob: str | None = None
        # Operation-marker probe state: the kind the probe reports, or an
        # unreadable probe when ``operation_probe_unreadable``.
        self.operation_kind: InProgressKind = InProgressKind.NONE
        self.operation_probe_unreadable = False

    # -- observation ------------------------------------------------------

    def _blob(self) -> str:
        rows: list[str] = []
        paths = sorted(set(self.committed) | set(self.worktree) | set(self.staged))
        for path in paths:
            head = self.committed.get(path, _MISSING)
            worktree = self.worktree.get(path, _MISSING)
            staged = self.staged.get(path, head)
            if worktree is _MISSING and staged == head:
                rows.append(f" D {path}\0")
            elif staged != head and worktree == staged:
                rows.append(f"M  {path}\0")
            elif staged != head:
                rows.append(f"MM {path}\0")
            elif worktree != head:
                rows.append(f" M {path}\0")
        for path in sorted(self.untracked):
            rows.append(f"?? {path}\0")
        return "".join(rows)

    def porcelain_status_z(self, root: Path) -> str | None:
        if self.fail_reobserve:
            return None
        if self.forced_blob is not None:
            return self.forced_blob
        return self._blob()

    def tracked_diff_hash(self, root: Path) -> str | None:
        if self.fail_reobserve:
            return None
        return _diff_hash(self.worktree, self.committed)

    def head_sha(self, root: Path) -> str | None:
        return self.head

    def in_progress_kind(self, root: Path) -> Reading[InProgressKind]:
        if self.operation_probe_unreadable:
            return Reading[InProgressKind].unknown("git directory unreadable")
        return Reading.known(self.operation_kind)

    # -- mutations ---------------------------------------------------------

    def snapshot_worktree(self, root: Path, *, head_sha: str) -> str:
        self.snapshot_count += 1
        ref = f"refs/ralph-backup/fake-{self.snapshot_count:02d}"
        self.existing_refs.add(ref)
        return ref

    def ref_exists(self, root: Path, ref: str) -> bool:
        return ref in self.existing_refs

    def restore_paths(self, root: Path, *, source: str, paths: Sequence[str]) -> bool:
        self.restore_calls.append((source, tuple(paths)))
        if self.fail_restore:
            return False
        for path in paths:
            if path in self.committed:
                self.worktree[path] = self.committed[path]
            self.staged.pop(path, None)
        return True

    def preserve_dirt_commit(
        self, root: Path, *, head_sha: str, paths: Sequence[str]
    ) -> str | None:
        self.preserve_calls.append(tuple(paths))
        ref = "refs/ralph-planning-residue/preserved-fake01"
        self.existing_refs.add(ref)
        return ref

    def quarantine_paths(
        self,
        root: Path,
        paths: Sequence[str],
        *,
        quarantine_dir: Path,
    ) -> tuple[str, ...]:
        self.quarantine_calls.append(tuple(paths))
        moved: list[str] = []
        for path in paths:
            if path in self.worktree:
                self.quarantined[path] = self.worktree.pop(path)
                moved.append(path)
            elif path in self.untracked:
                self.quarantined[path] = self.untracked.pop(path)
                moved.append(path)
        return tuple(moved)

    def incoming_overwrite_paths(self, root: Path, target: str | None) -> frozenset[str]:
        return self.incoming

    # -- state -------------------------------------------------------------

    def load_state(self, root: Path) -> Mapping[str, Any] | None:
        return self.state

    def store_state(self, root: Path, payload: Mapping[str, Any]) -> None:
        self.state = dict(payload)

    def clear_state(self, root: Path) -> None:
        self.state = None

    # -- landing restore ---------------------------------------------------

    def path_exists(self, path: Path) -> bool:
        return str(path) in {str(_ROOT / p) for p in (*self.worktree, *self.untracked)}

    def move_path(self, source: str, destination: Path) -> None:
        rel = str(destination).removeprefix(str(_ROOT) + "/")
        for path, content in list(self.quarantined.items()):
            if path == rel:
                self.quarantined.pop(path)
                self.untracked[path] = content
                return
        raise OSError(f"no quarantined content for {rel}")


class _FakeLedger:
    """In-memory ResidueLedgerPort returning a scripted action sequence."""

    def __init__(self, actions: Sequence[NextAction] | None = None) -> None:
        self.actions = list(actions or [NextAction.SAVE_AND_RESTORE])
        self.charged: list[tuple[tuple[str, ...], tuple[str, ...]]] = []
        self.attributions: list[DirtAttribution] = []

    def charge_residue_attempt(self, root: Path, *, residue: Any) -> NextAction:
        self.charged.append((residue.tracked_paths, residue.ambiguous_paths))
        self.attributions.append(residue_attribution(residue))
        index = min(len(self.charged) - 1, len(self.actions) - 1)
        return self.actions[index]


def _baseline(git: _FakeCleanupGit) -> PlanningBaseline:
    baseline = capture_planning_baseline(_ROOT, git=git)
    assert baseline is not None
    return baseline


def _run(
    git: _FakeCleanupGit,
    baseline: PlanningBaseline,
    ledger: _FakeLedger | None = None,
    warns: list[str] | None = None,
) -> ResidueCleanupOutcome:
    return run_residue_cleanup(
        _ROOT,
        baseline=baseline,
        git=git,
        ledger=ledger if ledger is not None else _FakeLedger(),
        target="main",
        warn=warns.append if warns is not None else None,
    )


# ---------------------------------------------------------------------------
# Baseline capture.
# ---------------------------------------------------------------------------


def test_baseline_capture_records_head_status_and_fingerprint() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    assert baseline.head_sha == "head-sha-1"
    assert " M src/app.py" not in baseline.status_blob
    assert baseline.fingerprint == compute_fingerprint(
        head_sha="head-sha-1",
        tracked_entries={},
        tracked_diff_hash=baseline.tracked_diff_hash,
    )
    assert baseline.human_work_ref is None
    assert git.snapshot_count == 0


def test_baseline_snapshots_pre_existing_human_work() -> None:
    git = _FakeCleanupGit()
    git.worktree["src/app.py"] = "human edit"
    baseline = _baseline(git)
    assert baseline.human_work_ref == "refs/ralph-backup/fake-01"
    assert git.snapshot_count == 1


def test_baseline_capture_none_when_unreadable() -> None:
    git = _FakeCleanupGit()
    git.fail_reobserve = True
    assert capture_planning_baseline(_ROOT, git=git) is None


# ---------------------------------------------------------------------------
# Pure residue detection.
# ---------------------------------------------------------------------------


def _clean_baseline() -> PlanningBaseline:
    git = _FakeCleanupGit()
    return _baseline(git)


def test_detect_residue_none_when_unchanged() -> None:
    baseline = _clean_baseline()
    assert (
        detect_planning_residue(
            baseline, baseline.status_blob, after_diff_hash=baseline.tracked_diff_hash
        )
        is None
    )


def test_detect_residue_new_tracked_path() -> None:
    baseline = _clean_baseline()
    after = " M src/app.py\0"
    residue = detect_planning_residue(baseline, after, after_diff_hash="different")
    assert residue is not None
    assert residue.tracked_paths == ("src/app.py",)
    assert residue.ambiguous_paths == ()
    assert residue.untracked_paths == ()


def test_detect_residue_xy_change_is_ambiguous() -> None:
    baseline_git = _FakeCleanupGit()
    baseline_git.worktree["src/app.py"] = "human"
    baseline = _baseline(baseline_git)
    after = "M  src/app.py\0"  # planning staged the human-edited file
    residue = detect_planning_residue(baseline, after, after_diff_hash="different")
    assert residue is not None
    assert residue.tracked_paths == ()
    assert residue.ambiguous_paths == ("src/app.py",)


def test_detect_residue_content_only_change_is_ambiguous() -> None:
    baseline_git = _FakeCleanupGit()
    baseline_git.worktree["src/app.py"] = "human-v1"
    baseline = _baseline(baseline_git)
    after = " M src/app.py\0"  # same porcelain row, content moved underneath
    residue = detect_planning_residue(baseline, after, after_diff_hash="moved")
    assert residue is not None
    assert residue.ambiguous_paths == ("src/app.py",)


def test_detect_residue_vanished_baseline_dirt_is_ambiguous() -> None:
    baseline_git = _FakeCleanupGit()
    baseline_git.worktree["src/app.py"] = "human"
    baseline = _baseline(baseline_git)
    residue = detect_planning_residue(baseline, "", after_diff_hash="now-clean")
    assert residue is not None
    assert residue.ambiguous_paths == ("src/app.py",)


def test_detect_residue_agent_artifacts_not_residue() -> None:
    baseline = _clean_baseline()
    after = baseline.status_blob + "?? .agent/plan/out.md\0"
    assert (
        detect_planning_residue(baseline, after, after_diff_hash=baseline.tracked_diff_hash) is None
    )


# ---------------------------------------------------------------------------
# The executor against the decision table's vocabulary.
# ---------------------------------------------------------------------------


def test_residue_observation_maps_to_blocking_shape() -> None:
    baseline = _clean_baseline()
    residue = detect_planning_residue(baseline, " M src/app.py\0", after_diff_hash="x")
    assert residue is not None
    observation, shape = residue_observation(residue)
    assert shape is DirtShape.UNSTAGED_MODIFICATION
    assert residue_attribution(residue) is DirtAttribution.UNOWNED
    decision = decide(
        DecisionContext(
            observation=observation,
            attempts=AttemptState(),
            dirt_shape=shape,
            dirt_attribution=DirtAttribution.UNOWNED,
        )
    )
    assert decision is NextAction.SAVE_AND_RESTORE


def test_repeat_pressure_escalates_down_cleanup_ladder() -> None:
    baseline = _clean_baseline()
    residue = detect_planning_residue(baseline, " M src/app.py\0", after_diff_hash="x")
    assert residue is not None
    observation, shape = residue_observation(residue)
    signature = AttemptState().signature(observation)
    pressured = AttemptState(
        repeat_counts=OrderedDict({(signature, NextAction.SAVE_AND_RESTORE.value): 2}),
    )
    decision = decide(
        DecisionContext(
            observation=observation,
            attempts=pressured,
            dirt_shape=shape,
            dirt_attribution=DirtAttribution.UNOWNED,
        )
    )
    assert decision is NextAction.PRESERVE_VIA_TEMP_INDEX


def test_ambiguous_attribution_is_unknown_save_never_revert() -> None:
    baseline_git = _FakeCleanupGit()
    baseline_git.worktree["src/app.py"] = "human edit"
    baseline = _baseline(baseline_git)
    # Planning staged the human-edited file: attribution cannot separate
    # the human edit from planning's staging.
    residue = detect_planning_residue(baseline, "M  src/app.py\0", after_diff_hash="x")
    assert residue is not None
    assert residue.ambiguous_paths == ("src/app.py",)
    _observation, shape = residue_observation(residue)
    assert residue_attribution(residue) is DirtAttribution.UNKNOWN
    decision = decide(
        DecisionContext(
            observation=_observation,
            attempts=AttemptState(),
            dirt_shape=shape,
            dirt_attribution=DirtAttribution.UNKNOWN,
        )
    )
    assert decision is NextAction.SAVE_AND_RESTORE


# ---------------------------------------------------------------------------
# Executor behaviour.
# ---------------------------------------------------------------------------


def test_clean_dispatch_runs_no_cleanup() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    ledger = _FakeLedger()
    warns: list[str] = []
    outcome = _run(git, baseline, ledger, warns)
    assert outcome.clean
    assert ledger.charged == []
    assert git.snapshot_count == 0
    assert git.restore_calls == []
    assert warns == []


def test_tracked_residue_saved_restored_and_warned_once() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "planning wrote here"
    ledger = _FakeLedger()
    warns: list[str] = []
    outcome = _run(git, baseline, ledger, warns)
    assert outcome.action is NextAction.SAVE_AND_RESTORE
    assert outcome.backup_ref == "refs/ralph-backup/fake-01"
    assert git.snapshot_count == 1
    assert git.restore_calls == [("head-sha-1", ("src/app.py",))]
    assert outcome.restored_paths == ("src/app.py",)
    assert outcome.remaining_paths == ()
    assert git.worktree["src/app.py"] == "base"
    assert len(ledger.charged) == 1
    assert len(warns) == 1
    assert "src/app.py" in warns[0]
    assert "refs/ralph-backup/fake-01" in warns[0]
    # Completed and nothing quarantined: progress state is retired.
    assert git.state is None


def test_untracked_only_residue_does_not_block() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.untracked["scratch.md"] = "notes"
    ledger = _FakeLedger()
    outcome = _run(git, baseline, ledger)
    assert outcome.untracked_only
    assert ledger.charged == []
    assert git.restore_calls == []
    assert git.untracked["scratch.md"] == "notes"


def test_overwritten_untracked_is_quarantined_and_restoreable_after_landing() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.untracked["docs/spec.md"] = "untracked spec"
    git.incoming = frozenset({"docs/spec.md"})
    ledger = _FakeLedger()
    warns: list[str] = []
    outcome = _run(git, baseline, ledger, warns)
    assert outcome.action is NextAction.SAVE_AND_RESTORE
    assert outcome.quarantined_untracked == ("docs/spec.md",)
    assert git.untracked == {}
    assert git.quarantined == {"docs/spec.md": "untracked spec"}
    assert git.state is not None  # progress survives: landing owes the restore
    restored = restore_quarantined_untracked(_ROOT, git=git, emit=warns.append)
    assert restored == ("docs/spec.md",)
    assert git.untracked["docs/spec.md"] == "untracked spec"
    assert git.state is None


def test_landing_restore_skips_paths_the_landing_recreated() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.untracked["docs/spec.md"] = "untracked spec"
    git.incoming = frozenset({"docs/spec.md"})
    _run(git, baseline, _FakeLedger())
    # The landing content now occupies the destination: the preserved copy
    # must stay quarantined rather than clobber it.
    git.untracked["docs/spec.md"] = "landed content"
    warns: list[str] = []
    restored = restore_quarantined_untracked(_ROOT, git=git, emit=warns.append)
    assert restored == ()
    assert git.untracked["docs/spec.md"] == "landed content"
    assert git.state is not None


def test_unmerged_residue_is_never_touched() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "planning wrote here"
    git.forced_blob = "UU src/app.py\0"
    residue = detect_planning_residue(baseline, "UU src/app.py\0", after_diff_hash="x")
    assert residue is not None
    assert residue.unmerged_paths == ("src/app.py",)
    ledger = _FakeLedger()
    outcome = _run(git, baseline, ledger)
    assert outcome.unmerged
    assert ledger.charged == []
    assert git.restore_calls == []
    assert git.worktree["src/app.py"] == "planning wrote here"


def test_all_resolved_merge_with_staged_resolution_blocks_cleanup() -> None:
    # The defect this gate exists for: MERGE_HEAD is present and every
    # conflict is already resolved and staged — porcelain shows only a
    # staged row, no unmerged entry. Cleanup must NOT restore the baseline
    # over the operation's staged resolution.
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "resolved merge"
    git.staged["src/app.py"] = "resolved merge"
    git.operation_kind = InProgressKind.MERGE
    ledger = _FakeLedger()
    warns: list[str] = []
    outcome = _run(git, baseline, ledger, warns)
    assert outcome.operation_blocked
    assert outcome.operation_kind is InProgressKind.MERGE
    assert not outcome.clean
    assert ledger.charged == []
    assert git.snapshot_count == 0
    assert git.restore_calls == []
    # The operation's staged resolution is untouched.
    assert git.staged["src/app.py"] == "resolved merge"
    assert git.worktree["src/app.py"] == "resolved merge"
    assert any("merge" in line for line in warns)


@pytest.mark.parametrize(
    "kind",
    [kind for kind in InProgressKind if kind is not InProgressKind.NONE],
    ids=[kind.value for kind in InProgressKind if kind is not InProgressKind.NONE],
)
def test_operation_marker_blocks_cleanup(kind: InProgressKind) -> None:
    # Every non-NONE kind blocks cleanup: the gate branches on "some
    # operation vs NONE", never on a specific kind, so the parametrization
    # over the whole vocabulary proves the label map and the gate stay
    # total when the enum grows.
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "operation staged this"
    git.staged["src/app.py"] = "operation staged this"
    git.operation_kind = kind
    ledger = _FakeLedger()
    outcome = _run(git, baseline, ledger)
    assert outcome.operation_blocked
    assert outcome.operation_kind is kind
    assert ledger.charged == []
    assert git.snapshot_count == 0
    assert git.restore_calls == []
    assert git.staged["src/app.py"] == "operation staged this"


def test_unreadable_operation_probe_fails_closed_without_restore() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "planning wrote here"
    git.operation_probe_unreadable = True
    ledger = _FakeLedger()
    warns: list[str] = []
    outcome = _run(git, baseline, ledger, warns)
    assert outcome.operation_unknown
    assert outcome.operation_kind is None
    assert not outcome.clean
    assert ledger.charged == []
    assert git.snapshot_count == 0
    assert git.restore_calls == []
    assert git.worktree["src/app.py"] == "planning wrote here"
    assert warns


def test_head_moved_refuses_restore_without_charge() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.head = "head-sha-2"
    git.worktree["src/app.py"] = "planning wrote here"
    ledger = _FakeLedger()
    outcome = _run(git, baseline, ledger)
    assert outcome.head_moved
    assert ledger.charged == []
    assert git.restore_calls == []
    assert git.snapshot_count == 0


def test_unreadable_observation_fails_closed() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.fail_reobserve = True
    outcome = _run(git, baseline, _FakeLedger())
    assert outcome.unreadable


def test_non_cleanup_action_is_refused() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "planning wrote here"
    outcome = _run(git, baseline, _FakeLedger(actions=[NextAction.COMMIT]))
    assert outcome.refused
    assert git.restore_calls == []
    assert git.snapshot_count == 0
    assert git.worktree["src/app.py"] == "planning wrote here"


def test_resume_after_interruption_skips_second_backup() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "planning wrote here"
    git.fail_restore = True
    ledger = _FakeLedger()
    first = _run(git, baseline, ledger)
    assert first.remaining_paths == ("src/app.py",)
    assert first.backup_ref == "refs/ralph-backup/fake-01"
    assert git.state is not None  # progress persisted mid-cleanup

    # Kill + restart: same residue fingerprint, backup ref still exists.
    git.fail_restore = False
    second = _run(git, baseline, ledger)
    assert second.resumed
    assert second.backup_ref == "refs/ralph-backup/fake-01"
    assert git.snapshot_count == 1  # NO second backup of the same content
    assert git.restore_calls[1] == ("head-sha-1", ("src/app.py",))
    assert second.remaining_paths == ()
    assert git.worktree["src/app.py"] == "base"
    assert git.state is None


def test_escalated_rung_preserves_via_temp_index_before_restore() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "planning wrote here"
    outcome = _run(git, baseline, _FakeLedger(actions=[NextAction.PRESERVE_VIA_TEMP_INDEX]))
    assert outcome.action is NextAction.PRESERVE_VIA_TEMP_INDEX
    assert git.preserve_calls == [("src/app.py",)]
    assert git.restore_calls == [("head-sha-1", ("src/app.py",))]
    assert git.worktree["src/app.py"] == "base"


def test_quarantine_rung_moves_files_aside_before_restore() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "planning wrote here"
    outcome = _run(git, baseline, _FakeLedger(actions=[NextAction.QUARANTINE_AND_RESTORE]))
    assert outcome.action is NextAction.QUARANTINE_AND_RESTORE
    assert git.quarantine_calls == [("src/app.py",)]
    assert git.quarantined == {"src/app.py": "planning wrote here"}
    assert git.worktree["src/app.py"] == "base"


def test_degraded_rung_executes_at_quarantine_strength_and_reports() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "planning wrote here"
    warns: list[str] = []
    outcome = _run(git, baseline, _FakeLedger(actions=[NextAction.LAST_RESORT_RECOVERY]), warns)
    assert outcome.degraded
    assert outcome.action is NextAction.LAST_RESORT_RECOVERY
    assert git.quarantine_calls == [("src/app.py",)]
    assert git.worktree["src/app.py"] == "base"
    assert any("LAST_RESORT" in line or "last_resort" in line for line in warns)


def test_ambiguous_paths_saved_not_restored_and_reported() -> None:
    git = _FakeCleanupGit()
    git.worktree["src/app.py"] = "human edit"
    baseline = _baseline(git)
    git.staged["src/app.py"] = "human edit"  # planning staged the human file
    ledger = _FakeLedger()
    warns: list[str] = []
    outcome = _run(git, baseline, ledger, warns)
    assert outcome.ambiguous_paths == ("src/app.py",)
    assert outcome.restored_paths == ()
    assert git.restore_calls == []
    # Backup still happens: ambiguous work is saved, never discarded.
    assert outcome.backup_ref is not None
    assert ledger.attributions == [DirtAttribution.UNKNOWN]
    assert any("human work" in line and "NOT reverted" in line for line in warns)


def test_executor_never_raises_on_port_failure() -> None:
    git = _FakeCleanupGit()
    baseline = _baseline(git)

    class Exploding(_FakeCleanupGit):
        def porcelain_status_z(self, root: Path) -> str | None:
            raise RuntimeError("git exploded")

    outcome = _run(Exploding(), baseline, _FakeLedger())
    assert outcome.refused


def test_owner_ledger_port_charges_through_real_decision_table(tmp_path: Path) -> None:
    # The only real-I/O seam exercised here: the durable attempt ledger,
    # which owns its own tmp file. Verifies the production port's
    # load -> decide -> charge ordering end to end.
    baseline = PlanningBaseline(
        head_sha="head-sha-1",
        status_blob="",
        tracked_diff_hash="h",
        fingerprint=compute_fingerprint(
            head_sha="head-sha-1", tracked_entries={}, tracked_diff_hash="h"
        ),
        human_work_ref=None,
    )
    residue = PlanningResidue(
        fingerprint=baseline.fingerprint,
        tracked_paths=("src/app.py",),
        ambiguous_paths=(),
        untracked_paths=(),
        overwritten_untracked_paths=(),
        unmerged_paths=(),
    )
    port = OwnerResidueLedgerPort()
    action = port.charge_residue_attempt(tmp_path, residue=residue)
    assert action is NextAction.SAVE_AND_RESTORE
    second = port.charge_residue_attempt(tmp_path, residue=residue)
    assert second is NextAction.SAVE_AND_RESTORE
    third = port.charge_residue_attempt(tmp_path, residue=residue)
    assert third is NextAction.PRESERVE_VIA_TEMP_INDEX


def test_planning_residue_dataclass_defaults() -> None:
    outcome = ResidueCleanupOutcome()
    assert outcome.clean is False
    assert outcome.action is None
    assert outcome.restored_paths == ()

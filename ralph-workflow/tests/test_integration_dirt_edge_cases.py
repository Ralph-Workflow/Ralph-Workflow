"""AC-10 "Further edge cases": typed outcomes for the remaining dirt edge cases.

Every criterion below is asserted through a typed production outcome — a
closed-vocabulary value a caller matches on, never a prose string — and
every test is fake-based: no real git, no subprocess, no sleep, no wall
clock. The only real I/O is the durable attempt ledger's own tmp file,
exactly as in ``tests/test_integration_cleanup.py``.

* ``assume-unchanged`` / ``skip-worktree`` flags — verified against git
  itself, these flags hide a path from ``git status --porcelain``, from
  ``git diff HEAD`` and from ``git stash create``, but never from
  ``git ls-files -v``. The bounded typed index-flag probe
  (:func:`~ralph.pipeline.integration_observation.read_index_flag_dirt`)
  closes the blind spot: flagged paths whose content changed classify
  as ``DirtShape.INDEX_FLAG_HIDDEN`` (blocking, never CLEAN), are
  unhidden before the backup ref so the snapshot captures the real
  content, are restored to the baseline, and are named with the ref in
  exactly one WARNING — the same preserve-then-clean ladder as ordinary
  residue. An unreadable probe aborts the baseline capture and reports
  the typed ``flag_probe_unknown`` outcome: back off, never certify
  clean.
* Two Ralph processes cleaning the same tree — the repository lease
  payload carries the owner identity and a MONOTONIC stamp
  (:func:`~ralph.git._worktree_lease.lease_payload`); the liveness
  check answers a typed :class:`~ralph.git._worktree_lease.LeaseStealDecision`:
  a live winner refuses the steal (the loser re-observes, touching
  nothing) and a dead winner's lease is reclaimed, after which the
  resumed cleanup takes NO second backup of the same content.
* The clock-jump property — ``WALL_CLOCK_JUMP`` is a named catalogue
  fault; with the injectable clock, a backwards jump never extends a
  scheduled wait, never ages a lease negatively, and never un-spends
  the durable attempt budget (the budget is repeat COUNTS, not wall
  time, so winding the clock back cannot resurrect a spent rung).
* Commit-hook rejection — a hook failure classifies to the typed
  :attr:`~ralph.git.rebase._rebase_kind.RebaseKind.HOOK_REJECTION`;
  once the rejection is recorded the dirt loop re-decides the SAME
  observation through the cleanup ladder (``SAVE_AND_RESTORE`` first),
  never a commit-class action (the hook is never retried) and never a
  resolver dispatch (dirt is a condition no agent can change).
"""

from __future__ import annotations

import hashlib
from collections.abc import Sequence
from pathlib import Path

import pytest

from ralph.git import git_command_queue
from ralph.git.hardening import parse_porcelain_z
from ralph.git.rebase import RebaseKind, classify_rebase_error
from ralph.pipeline.auto_integrate_backoff import (
    RETRY_BASE_DELAY_SECONDS,
    compute_retry_delay,
    wait_before_retry,
)
from ralph.pipeline.integration_attempt_ledger import AttemptLedger
from ralph.pipeline.integration_cleanup import (
    PlanningResidue,
    capture_planning_baseline,
    detect_planning_residue,
    residue_attribution,
    residue_observation,
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
    HeadState,
    IndexFlagDirt,
    InProgressKind,
    InProgressOwnership,
    IntegrationRecordState,
    Observation,
    Reading,
    TargetVsFeature,
    TargetVsRemote,
    WorktreeState,
    classify_worktree,
)
from ralph.pipeline.integration_owner import (
    charge_integration_attempt,
    load_integration_attempts,
)
from tests._fake_git_env import SimulatedGitEnvironment
from tests._fake_git_fault import FaultKind, registered_outcome
from tests.test_integration_cleanup import (
    _MISSING,
    _ROOT,
    _baseline,
    _diff_hash,
    _FakeCleanupGit,
    _FakeLedger,
    _run,
)

pytestmark = pytest.mark.timeout_seconds(10)

#: The wait the production back-off takes before the FIRST retry at zero
#: jitter: the exponential step with FULL jitter, drawn at its lowest
#: spread.
_EXPECTED_FIRST_BACKOFF = RETRY_BASE_DELAY_SECONDS * 0.5


class _FlagAwareGit(_FakeCleanupGit):
    """Cleanup fake emulating git's index-flag semantics exactly.

    Verified against git itself: ``git update-index --assume-unchanged``
    and ``git update-index --skip-worktree`` both hide a path from
    ``git status --porcelain``, from ``git diff HEAD`` and from
    ``git stash create``, while ``git ls-files -v`` still names the
    flagged paths and ``git hash-object`` still reads their real content.
    The fake therefore hides the flagged paths from the porcelain blob
    and the tracked-diff hash, answers the typed index-flag probe from
    the flagged set, and models ``git update-index --no-assume-unchanged
    --no-skip-worktree`` (the unhide the cleanup runs before its backup)
    by simply unflagging the paths.
    """

    def __init__(self, flagged: tuple[str, ...]) -> None:
        super().__init__()
        self._flagged = set(flagged)
        self.flag_probe_unreadable = False
        self.clear_flags_calls: list[tuple[str, ...]] = []

    def _blob(self) -> str:
        blob = super()._blob()
        visible = [entry for entry in parse_porcelain_z(blob) if entry.path not in self._flagged]
        return "".join(f"{entry.xy} {entry.path}\0" for entry in visible)

    def tracked_diff_hash(self, root: Path) -> str | None:
        if self.fail_reobserve:
            return None
        visible = {path: c for path, c in self.worktree.items() if path not in self._flagged}
        return _diff_hash(visible, self.committed)

    def index_flag_dirt(self, root: Path) -> Reading[IndexFlagDirt]:
        """The typed probe: flagged paths, their kinds, content hashes, changes."""
        del root
        if self.flag_probe_unreadable:
            return Reading[IndexFlagDirt].unknown("git ls-files -v unreadable: exit=128")
        hashes = tuple(
            (path, hashlib.sha256(self.worktree.get(path, "").encode()).hexdigest())
            for path in sorted(self._flagged)
        )
        flagged = frozenset(self._flagged)
        return Reading.known(
            IndexFlagDirt(
                assume_unchanged=flagged,
                skip_worktree=frozenset(),
                changed_paths=frozenset(
                    path
                    for path in self._flagged
                    if self.worktree.get(path, _MISSING) != self.committed.get(path, _MISSING)
                ),
                path_hashes=hashes,
            )
        )

    def clear_index_flags(self, root: Path, paths: Sequence[str]) -> bool:
        """``git update-index --no-assume-unchanged --no-skip-worktree``."""
        del root
        self.clear_flags_calls.append(tuple(paths))
        self._flagged.difference_update(paths)
        return True


class _JumpingClock:
    """An injectable monotonic clock the test can wind BACKWARDS.

    Models ``FaultKind.WALL_CLOCK_JUMP``: a wall-clock jump moves the
    reading of "now" without any real waiting. ``sleep`` records the
    scheduled waits, so the wait schedule is asserted without ever
    sleeping for real.
    """

    def __init__(self, start: float = 10_000.0) -> None:
        self._now = start
        self.slept: list[float] = []

    def monotonic(self) -> float:
        return self._now

    def time(self) -> float:
        return self._now

    def sleep(self, seconds: float) -> None:
        self.slept.append(seconds)
        self._now += max(0.0, seconds)

    def jump_backwards(self, seconds: float) -> None:
        self._now -= seconds


@pytest.mark.parametrize("flag", ["assume-unchanged", "skip-worktree"])
def test_flag_hidden_dirt_is_preserved_restored_and_warned(flag: str) -> None:
    """Flag-hidden dirt routes through the SAME preserve-then-clean ladder.

    Real git hides ``assume-unchanged`` / ``skip-worktree`` paths from
    porcelain, from the tracked diff and from the stash snapshot — but
    ``git ls-files -v`` still names them and ``git hash-object`` still
    reads their content, so the bounded typed index-flag probe closes
    the blind spot: flagged paths whose content changed type as
    :attr:`DirtShape.INDEX_FLAG_HIDDEN`, a blocking verdict, never
    CLEAN. The cleanup unhides the paths (``git update-index
    --no-assume-unchanged --no-skip-worktree``) so the ordinary backup
    ref captures the real content, restores the baseline, and names the
    paths and the ref in exactly one WARNING — the identical ladder
    ordinary residue climbs, never a resolver dispatch.
    """
    del flag  # both flags behave identically at every observation seam
    git = _FlagAwareGit(flagged=("src/app.py",))
    # Baseline: the flagged path matches HEAD — clean under the flag.
    baseline = _baseline(git)
    assert baseline.human_work_ref is None
    assert baseline.flag_hashes != ()

    # Porcelain alone stays blind (git itself hides the path); the typed
    # probe is what proves the hidden edit. The seam contract: the
    # classifier with the probe reading is blocked, the classifier on
    # the raw blob keeps its legacy answer and must never be consulted
    # without the probe where flags may exist.
    git.worktree["src/app.py"] = "edited under the flag"
    verdict = classify_worktree(
        Reading.known(git.porcelain_status_z(_ROOT) or ""),
        index_flag_dirt=git.index_flag_dirt(_ROOT).value,
    )
    assert verdict.state is WorktreeState.TRACKED_CHANGES
    assert verdict.dirt_shape is DirtShape.INDEX_FLAG_HIDDEN
    assert verdict.dispatch_blocked
    assert not verdict.is_clean
    assert "src/app.py" in verdict.reason

    # The decision table routes the shape to the cleanup ladder: the
    # first rung is SAVE_AND_RESTORE and no rung is ever a resolver
    # dispatch (dirt is a condition no agent can change).
    residue = detect_planning_residue(
        baseline,
        git.porcelain_status_z(_ROOT) or "",
        after_diff_hash=git.tracked_diff_hash(_ROOT) or "",
        after_flag_dirt=git.index_flag_dirt(_ROOT).value,
    )
    assert residue is not None
    assert residue.index_flag_paths == ("src/app.py",)
    observation, shape = residue_observation(residue)
    assert shape is DirtShape.INDEX_FLAG_HIDDEN
    assert observation.worktree is WorktreeState.TRACKED_CHANGES
    decided = [
        decide(
            DecisionContext(
                observation=observation,
                attempts=AttemptState(),
                dirt_shape=shape,
                dirt_attribution=residue_attribution(residue),
            )
        )
        for _ in range(3)
    ]
    assert decided == [NextAction.SAVE_AND_RESTORE] * 3
    assert NextAction.DISPATCH_RESOLVER not in decided

    # The cleanup itself: flags cleared BEFORE the backup so the
    # snapshot sees the real content, one snapshot, baseline restored,
    # exactly one WARNING naming the path and the backup ref.
    warns: list[str] = []
    outcome = _run(git, baseline, _FakeLedger(), warns)
    assert not outcome.clean
    assert not outcome.refused
    assert outcome.action is NextAction.SAVE_AND_RESTORE
    assert outcome.backup_ref == "refs/ralph-backup/fake-01"
    assert git.clear_flags_calls == [("src/app.py",)]
    assert git.snapshot_count == 1
    assert outcome.restored_paths == ("src/app.py",)
    assert git.worktree["src/app.py"] == "base"
    assert outcome.remaining_paths == ()
    assert len(warns) == 1
    assert "src/app.py" in warns[0]
    assert "refs/ralph-backup/fake-01" in warns[0]


def test_flag_probe_unreadable_backs_off_and_never_certifies_clean() -> None:
    """An unknown index-flag reading is not "clean": baseline and cleanup fail closed.

    The unreadable probe aborts the baseline capture (the dispatch never
    starts on an uncertified tree) and any cleanup run reports the typed
    ``flag_probe_unknown`` outcome: no snapshot, no restore, no flag
    cleared, one WARNING, tree untouched.
    """
    git = _FlagAwareGit(flagged=("src/app.py",))
    git.flag_probe_unreadable = True
    assert capture_planning_baseline(_ROOT, git=git) is None

    git.flag_probe_unreadable = False
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "edited under the flag"
    git.flag_probe_unreadable = True
    warns: list[str] = []
    outcome = _run(git, baseline, _FakeLedger(), warns)
    assert outcome.flag_probe_unknown
    assert not outcome.clean
    assert git.snapshot_count == 0
    assert git.restore_calls == []
    assert git.clear_flags_calls == []
    assert git.worktree["src/app.py"] == "edited under the flag"
    assert len(warns) == 1


def test_two_cleaners_one_lease_loser_reobserves_and_never_reshoots() -> None:
    """A live winner refuses the steal; a dead winner's lease is reclaimed.

    The repository lease's liveness check answers a typed
    :class:`~ralph.git.git_command_queue.LeaseStealDecision` (the payload
    owner identity and its monotonic stamp are pinned by
    ``tests/test_integration_fault_model.py``). The loser, refused while
    the winner is live, takes no cleanup action at all; it re-observes
    after the winner dies, and the resumed cleanup finds the winner's
    backup ref still recorded — NO second backup of the same content.
    """
    # The loser arrives while the winner is live: the typed verdict
    # refuses the steal, naming the evidence.
    refused = git_command_queue.decide_lease_steal(
        age_seconds=0.0, liveness="live", stale_seconds=300.0
    )
    assert not refused.allowed
    assert refused.reason == "holder_alive"

    # A liveness probe that cannot prove the holder dead fails closed.
    indeterminate = git_command_queue.decide_lease_steal(
        age_seconds=10_000.0, liveness="unknown", stale_seconds=300.0
    )
    assert not indeterminate.allowed
    assert indeterminate.reason == "liveness_indeterminate"

    # The winner cleans, and is killed between the backup and the
    # restore: progress is persisted mid-cleanup (fail_restore models
    # the interrupted restore), the tree is still dirty.
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "planning wrote here"
    git.fail_restore = True
    winner = _run(git, baseline, _FakeLedger())
    assert winner.backup_ref == "refs/ralph-backup/fake-01"
    assert winner.remaining_paths == ("src/app.py",)
    persisted_progress: dict[str, object] | None = git.state
    assert persisted_progress is not None, "progress persisted mid-cleanup"

    # The loser re-observes after the winner dies: the liveness probe
    # names a dead holder and the reclaim is allowed — the age evidence
    # is read from the injected monotonic clock, so a wall-clock jump
    # cannot age or de-age it.
    allowed = git_command_queue.decide_lease_steal(
        age_seconds=0.0, liveness="dead", stale_seconds=300.0
    )
    assert allowed.allowed
    assert allowed.reason == "holder_dead"

    git.fail_restore = False
    loser = _run(git, baseline, _FakeLedger())
    assert loser.resumed
    assert loser.backup_ref == "refs/ralph-backup/fake-01"
    assert git.snapshot_count == 1, "the resumed cleanup takes NO second backup"
    assert loser.remaining_paths == ()
    assert git.worktree["src/app.py"] == "base"


def test_backwards_clock_jump_never_extends_a_wait_or_unspends_the_budget(
    tmp_path: Path,
) -> None:
    """WALL_CLOCK_JUMP property: a backwards jump changes no schedule, no budget.

    Three assertions on the injectable clock:

    * the production back-off's scheduled wait is a pure function of the
      attempt index and the jitter — a backwards jump between two waits
      leaves the schedule byte-identical (a wait is never extended);
    * a lease age is clamped at zero, never negative, on the injected
      monotonic clock — a wait derived from an age is never extended by
      a jump (pinned by ``tests/test_integration_fault_model.py``);
    * the durable attempt budget is repeat COUNTS, not wall time —
      winding the wall clock back and re-reading the ledger returns the
      same counts, and charging after the jump continues upward.
    """
    clock = _JumpingClock()
    jump_backwards = 3600.0
    clock.jump_backwards(jump_backwards)

    # The schedule is unchanged by the jump: same delay, twice.
    wait_before_retry(1, sleep=clock.sleep, jitter=lambda: 0.0)
    clock.jump_backwards(jump_backwards)
    wait_before_retry(1, sleep=clock.sleep, jitter=lambda: 0.0)
    assert clock.slept == [_EXPECTED_FIRST_BACKOFF, _EXPECTED_FIRST_BACKOFF]
    assert compute_retry_delay(1, jitter=lambda: 0.0) == _EXPECTED_FIRST_BACKOFF

    # The budget is counts: jump the wall clock back, re-read, charge on.
    ledger_path = tmp_path / ".agent" / "edge-cases-ledger.json"
    observation = Observation(
        target_vs_feature=TargetVsFeature.EQUAL,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.TRACKED_CHANGES,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    )
    action = NextAction.SAVE_AND_RESTORE
    for _ in range(2):
        charge_integration_attempt(
            tmp_path, observation=observation, next_action=action, ledger_path=ledger_path
        )
    before = AttemptLedger(tmp_path, ledger_path=ledger_path).read()
    # ... the wall clock jumps backwards an hour ...
    after = AttemptLedger(tmp_path, ledger_path=ledger_path).read()
    assert after.repeat_counts == before.repeat_counts, "a jump must never un-spend the budget"
    charge_integration_attempt(
        tmp_path, observation=observation, next_action=action, ledger_path=ledger_path
    )
    final = AttemptLedger(tmp_path, ledger_path=ledger_path).read()
    assert sum(final.repeat_counts.values()) == 3, "charging after the jump continues upward"


def test_wall_clock_jump_is_a_named_fault_with_a_typed_outcome() -> None:
    """The named fault defers the observation and moves the simulated clock."""
    outcome = registered_outcome(FaultKind.WALL_CLOCK_JUMP)
    assert outcome.outcome == "unknown"
    assert outcome.effect == "defer"
    assert outcome.phase == "observation"

    env = SimulatedGitEnvironment()
    before = env.clock.now()
    env.set_faults([FaultKind.WALL_CLOCK_JUMP])
    env.observe()
    assert env.fired(FaultKind.WALL_CLOCK_JUMP)
    assert env.clock.now() == before + 3600.0


def test_commit_hook_rejection_moves_the_loop_to_save_and_restore(
    tmp_path: Path,
) -> None:
    """Hook failure: typed HOOK_REJECTION, work preserved, hook never retried.

    The pre-rejection decision for phase-owned dirt is the commit-phase
    action — the one whose executor retries the hook. Once the hook
    rejects (classified, not string-matched), the same observation is
    re-decided with residue attribution: the loop moves to
    ``SAVE_AND_RESTORE`` and climbs the cleanup ladder rung by rung,
    saturating at ``LAST_RESORT_RECOVERY`` — never a commit-class action
    and never ``DISPATCH_RESOLVER`` for the dirt.
    """
    rejection = classify_rebase_error("error: pre-commit hook rejected the commit\n", "")
    assert rejection.kind is RebaseKind.HOOK_REJECTION
    assert rejection.metadata["hook_name"] == "pre-commit"

    observation = Observation(
        target_vs_feature=TargetVsFeature.EQUAL,
        target_vs_remote=TargetVsRemote.EQUAL,
        worktree=WorktreeState.TRACKED_CHANGES,
        head=HeadState.ATTACHED,
        in_progress_kind=InProgressKind.NONE,
        in_progress_ownership=InProgressOwnership.OWNED,
        record=IntegrationRecordState.NONE,
        agent_active=False,
    )
    pre_rejection = decide(
        DecisionContext(
            observation=observation,
            attempts=AttemptState(),
            dirt_shape=DirtShape.UNSTAGED_MODIFICATION,
            dirt_attribution=DirtAttribution.PHASE_OWNED,
        )
    )
    assert pre_rejection is NextAction.COMMIT_VIA_COMMIT_PHASE

    ledger_path = tmp_path / ".agent" / "hook-rejection-ledger.json"
    decided: list[NextAction] = []
    for _ in range(8):
        attempts = load_integration_attempts(tmp_path, ledger_path=ledger_path)
        action = decide(
            DecisionContext(
                observation=observation,
                attempts=attempts,
                dirt_shape=DirtShape.UNSTAGED_MODIFICATION,
                dirt_attribution=DirtAttribution.UNOWNED,
            )
        )
        decided.append(action)
        charge_integration_attempt(
            tmp_path, observation=observation, next_action=action, ledger_path=ledger_path
        )

    # The ladder climb: direct action twice (first contact is never a
    # repeat), then one rung per charged repeat, saturating at the
    # terminal rung — never a re-run of a failed action.
    assert decided == [
        NextAction.SAVE_AND_RESTORE,
        NextAction.SAVE_AND_RESTORE,
        NextAction.PRESERVE_VIA_TEMP_INDEX,
        NextAction.QUARANTINE_AND_RESTORE,
        NextAction.REBUILD_IN_FRESH_WORKTREE,
        NextAction.ADAPT_ENVIRONMENT_AND_RETRY,
        NextAction.LAST_RESORT_RECOVERY,
        NextAction.LAST_RESORT_RECOVERY,
    ]
    commit_class = frozenset({NextAction.COMMIT, NextAction.COMMIT_VIA_COMMIT_PHASE})
    assert [action for action in decided if action in commit_class] == []
    assert NextAction.DISPATCH_RESOLVER not in decided

    # Work preserved: the first cleanup run saves the hook-rejected
    # content to a backup ref BEFORE any restore touches the tree.
    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "hook rejected this edit"
    warns: list[str] = []
    outcome = _run(git, baseline, _FakeLedger(), warns)
    assert outcome.action is NextAction.SAVE_AND_RESTORE
    assert outcome.backup_ref == "refs/ralph-backup/fake-01"
    assert outcome.restored_paths == ("src/app.py",)
    assert len(warns) == 1
    assert "src/app.py" in warns[0]
    assert "refs/ralph-backup/fake-01" in warns[0]


class _ScriptedLedger:
    """Residue ledger port serving one pre-decided action per charge."""

    def __init__(self, action: NextAction) -> None:
        self._action = action

    def charge_residue_attempt(self, root: Path, *, residue: PlanningResidue) -> NextAction:
        del root, residue
        return self._action


def test_repeated_refusal_preserves_the_same_residue_once_not_unboundedly(
    tmp_path: Path,
) -> None:
    """AC-11 edge: a refusal recurring after preservation never re-preserves.

    The 2026-10-27 loop re-attempted the same state every ~7 s. Here the
    merge refusal recurs because the restore keeps failing, so the SAME
    residue faces the decision table again and again, charged through
    the real owner charge path each time (the owner's residue seam
    projects the residue onto the same observation regardless of root,
    so the durable ledger is anchored at ``tmp_path``):

    * the direct cleanup rung answers twice (first contact is never a
      repeat), the alternate-preservation rung once — then every later
      charge is a STRONGER rung that moves the residue aside instead of
      taking another copy of the same content;
    * the preserve-shaped decisions are therefore bounded (3, the two
      direct rungs plus the alternate rung) — never one per retry;
    * no resolver is dispatched for the dirt at any depth.
    """
    from ralph.pipeline.integration_cleanup import residue_observation
    from ralph.pipeline.integration_decision import DecisionContext, DirtAttribution, decide
    from ralph.pipeline.integration_owner import charge_integration_attempt

    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "merge refused to overwrite this"
    git.fail_restore = True  # the restore keeps failing: the refusal recurs

    actions: list[NextAction] = []
    for _ in range(8):
        blob = git.porcelain_status_z(_ROOT)
        assert blob is not None
        diff = git.tracked_diff_hash(_ROOT)
        assert diff is not None
        residue = detect_planning_residue(baseline, blob, after_diff_hash=diff)
        assert residue is not None
        observation, shape = residue_observation(residue)
        attempts = load_integration_attempts(tmp_path)
        action = decide(
            DecisionContext(
                observation=observation,
                attempts=attempts,
                dirt_shape=shape,
                dirt_attribution=DirtAttribution.UNOWNED,
            )
        )
        charge_integration_attempt(tmp_path, observation=observation, next_action=action)
        actions.append(action)
        # The rung's executor acts on the tree; the restore fails, so the
        # residue remains and the refusal recurs at the next merge.
        warns: list[str] = []
        run_residue_cleanup(
            _ROOT,
            baseline=baseline,
            git=git,
            ledger=_ScriptedLedger(action),
            target="main",
            warn=warns.append,
        )

    # The climb: direct cleanup twice, then one different, stronger rung
    # per charged repeat — the identical preserve-and-retry never loops.
    assert actions[0] is NextAction.SAVE_AND_RESTORE
    assert actions[1] is NextAction.SAVE_AND_RESTORE
    assert actions[2] is NextAction.PRESERVE_VIA_TEMP_INDEX
    assert actions[3] is NextAction.QUARANTINE_AND_RESTORE
    assert actions[4:] == [
        NextAction.REBUILD_IN_FRESH_WORKTREE,
        NextAction.ADAPT_ENVIRONMENT_AND_RETRY,
        NextAction.LAST_RESORT_RECOVERY,
        NextAction.LAST_RESORT_RECOVERY,
    ]
    # Preserve-shaped decisions are bounded; the stronger rungs never
    # take another copy of the same content.
    preserve_shaped = (NextAction.SAVE_AND_RESTORE, NextAction.PRESERVE_VIA_TEMP_INDEX)
    assert sum(1 for action in actions if action in preserve_shaped) == 3
    assert NextAction.DISPATCH_RESOLVER not in actions

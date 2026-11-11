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

_EXPECTED_FIRST_BACKOFF = RETRY_BASE_DELAY_SECONDS * 0.5


class _FlagAwareGit(_FakeCleanupGit):
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
        del root
        self.clear_flags_calls.append(tuple(paths))
        self._flagged.difference_update(paths)
        return True


class _JumpingClock:
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
    del flag
    git = _FlagAwareGit(flagged=("src/app.py",))
    baseline = _baseline(git)
    assert baseline.human_work_ref is None
    assert baseline.flag_hashes != ()

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
    refused = git_command_queue.decide_lease_steal(
        age_seconds=0.0, liveness="live", stale_seconds=300.0
    )
    assert not refused.allowed
    assert refused.reason == "holder_alive"

    indeterminate = git_command_queue.decide_lease_steal(
        age_seconds=10_000.0, liveness="unknown", stale_seconds=300.0
    )
    assert not indeterminate.allowed
    assert indeterminate.reason == "liveness_indeterminate"

    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "planning wrote here"
    git.fail_restore = True
    winner = _run(git, baseline, _FakeLedger())
    assert winner.backup_ref == "refs/ralph-backup/fake-01"
    assert winner.remaining_paths == ("src/app.py",)
    persisted_progress: dict[str, object] | None = git.state
    assert persisted_progress is not None, "progress persisted mid-cleanup"

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
    clock = _JumpingClock()
    jump_backwards = 3600.0
    clock.jump_backwards(jump_backwards)

    wait_before_retry(1, sleep=clock.sleep, jitter=lambda: 0.0)
    clock.jump_backwards(jump_backwards)
    wait_before_retry(1, sleep=clock.sleep, jitter=lambda: 0.0)
    assert clock.slept == [_EXPECTED_FIRST_BACKOFF, _EXPECTED_FIRST_BACKOFF]
    assert compute_retry_delay(1, jitter=lambda: 0.0) == _EXPECTED_FIRST_BACKOFF

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
    after = AttemptLedger(tmp_path, ledger_path=ledger_path).read()
    assert after.repeat_counts == before.repeat_counts, "a jump must never un-spend the budget"
    charge_integration_attempt(
        tmp_path, observation=observation, next_action=action, ledger_path=ledger_path
    )
    final = AttemptLedger(tmp_path, ledger_path=ledger_path).read()
    assert sum(final.repeat_counts.values()) == 3, "charging after the jump continues upward"


def test_wall_clock_jump_is_a_named_fault_with_a_typed_outcome() -> None:
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
    def __init__(self, action: NextAction) -> None:
        self._action = action

    def charge_residue_attempt(self, root: Path, *, residue: PlanningResidue) -> NextAction:
        del root, residue
        return self._action


def test_repeated_refusal_preserves_the_same_residue_once_not_unboundedly(
    tmp_path: Path,
) -> None:
    from ralph.pipeline.integration_cleanup import residue_observation
    from ralph.pipeline.integration_decision import DecisionContext, DirtAttribution, decide
    from ralph.pipeline.integration_owner import charge_integration_attempt

    git = _FakeCleanupGit()
    baseline = _baseline(git)
    git.worktree["src/app.py"] = "merge refused to overwrite this"
    git.fail_restore = True

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
        warns: list[str] = []
        run_residue_cleanup(
            _ROOT,
            baseline=baseline,
            git=git,
            ledger=_ScriptedLedger(action),
            target="main",
            warn=warns.append,
        )

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
    preserve_shaped = (NextAction.SAVE_AND_RESTORE, NextAction.PRESERVE_VIA_TEMP_INDEX)
    assert sum(1 for action in actions if action in preserve_shaped) == 3
    assert NextAction.DISPATCH_RESOLVER not in actions

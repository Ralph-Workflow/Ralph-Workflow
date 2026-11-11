"""Durable crash-recovery proofs for the commit-review rejection lifecycle.

A commit-review rejection is a DURABLE lifecycle with four boundaries:

1. the proposal seal is written (``refs/ralph/proposals/<run>/<iteration>``);
2. the ``rollback_pending`` checkpoint is persisted BEFORE the destructive
   restore can begin;
3. the tree is restored (to the run-start baseline, or to ``HEAD``);
4. the pending flag is cleared durably.

A crash at ANY of those boundaries must leave a repository a RESUMED run can
converge from, and every boundary must converge on the SAME end state: the
tree back at the restore point, the rejected proposal still recoverable from
its ref, the run-start baseline intact, ignored files and ``.agent/`` runtime
state untouched, and NO second restore.

The proofs are DURABLE: the state the crash left behind is read back from
DISK (a fresh checkpoint load, the way a new process reads it) and the
resume is re-entered through the real run-loop seam
(:func:`ralph.pipeline.run_loop.resolve_initial_state`). Nothing here calls
an in-memory helper to "simulate" a restart.
"""

from __future__ import annotations

import functools
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git import proposal_seal
from ralph.git.proposal_seal import (
    diff_tree,
    preflight,
    seal_baseline,
    seal_pending,
)
from ralph.phases.commit_review import CommitReviewRejected
from ralph.pipeline.checkpoint import load as load_checkpoint
from ralph.pipeline.reducer import reduce
from ralph.pipeline.run_loop import resolve_initial_state
from ralph.pipeline.runner import (
    execute_pending_rollback,
    rollback_checkpoint_path_for_root,
    save_checkpoint_or_log,
)
from ralph.pipeline.state import PipelineState

if TYPE_CHECKING:
    from ralph.git.proposal_seal import Seal
    from ralph.policy.models import PolicyBundle

_DEFAULT_POLICY_DIR = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"

#: The commit-review phase whose verdict rejects the proposal.
_COMMIT_REVIEW_PHASE = "development_commit_review"

#: The run whose rejection the lifecycle drives.
_RUN_ID = "run-rollback-resume"

#: The iteration whose proposal is rejected.
_ITERATION = 1

#: The lifecycle boundaries a crash can land on, and how many restores the
#: RESUME still owes after the crash: a crash after the durable clear leaves
#: nothing to restore, every other boundary leaves the restore unfinished.
_CRASH_BOUNDARIES: dict[str, int] = {
    "after_seal": 1,
    "after_pending_checkpoint": 1,
    "during_restore": 1,
    "after_clear": 0,
}

#: The real restore, captured before any fault injection patches it.
_real_rollback = proposal_seal.rollback


class _SimulatedCrash(BaseException):
    """A process death.

    A crash is NOT an ``Exception``: no handler may convert it into a policy
    decision (a gate failure, a rollback refusal). The pipeline's rollback
    executor catches ``Exception`` and routes it to the failure route, so a
    crash that must leave the durable pending marker in place has to escape
    that handler exactly as a killed process does.
    """


@functools.cache
def _bundle() -> PolicyBundle:
    """Return the bundled default policy."""
    from ralph.policy.loader import load_policy

    return load_policy(_DEFAULT_POLICY_DIR)


def _prepare_workspace(root: Path) -> tuple[Path, Path, Path]:
    """Create the runtime state the rollback must NOT touch.

    Returns:
        The tracked file, the git-ignored log and the ``.agent/`` runtime
        state file of the workspace.
    """
    readme = root / "README.md"
    ignored_log = root / "activity.log"
    agent_state = root / ".agent" / "state.json"
    agent_state.parent.mkdir(parents=True, exist_ok=True)
    agent_state.write_text('{"phase": "development"}\n', encoding="utf-8")
    ignored_log.write_text("runtime log line\n", encoding="utf-8")
    # The log must be ignored BY GIT: an ignored file is outside the proposal
    # tree, so neither the seal nor the restore may touch it.
    exclude = root / ".git" / "info" / "exclude"
    exclude.write_text(exclude.read_text(encoding="utf-8") + "*.log\n", encoding="utf-8")
    return readme, ignored_log, agent_state


def _seal_run_start_baseline(root: Path) -> Seal:
    """Seal the run-start baseline: the user's uncommitted work.

    The run starts on a dirty tree, so the restore point is the baseline and
    not ``HEAD``. The user's untracked file is part of the baseline, so the
    rollback must bring it back instead of removing it.
    """
    user_file = root / "user_uncommitted.txt"
    user_file.write_text("user work that must survive\n", encoding="utf-8")
    baseline = seal_baseline(root, _RUN_ID)
    assert baseline is not None
    return baseline


def _apply_rejected_proposal(root: Path, readme: Path) -> Path:
    """Apply the rejected iteration's changes to the working tree."""
    readme.write_text("developer change\n", encoding="utf-8")
    added = root / "rejected_added.txt"
    added.write_text("rejected addition\n", encoding="utf-8")
    return added


def _pending_state(bundle: PolicyBundle, state: PipelineState, ref: str) -> PipelineState:
    """Return the rollback-pending state the rejection reducer produces."""
    pending, _ = reduce(
        state,
        CommitReviewRejected(reason="rejected proposal", ref=ref, hard=True),
        bundle.pipeline,
    )
    assert pending.rollback_pending is True
    assert pending.rollback_ref == ref
    return pending


def _crash_before_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kill the process after the durable pending marker, before the restore."""

    def crashing_rollback(*_args: object, **_kwargs: object) -> None:
        raise _SimulatedCrash("crash after the durable pending checkpoint")

    monkeypatch.setattr(proposal_seal, "rollback", crashing_rollback)


def _crash_during_restore(monkeypatch: pytest.MonkeyPatch) -> None:
    """Kill the process inside the restore, after the tree reset.

    The tracked files are already back at the restore point when the crash
    lands; the files the proposal ADDED are not removed yet. A resumed run
    must therefore repeat the restore instead of assuming it finished.
    """

    def crashing_rollback(
        repo_root: Path | str,
        ref: str,
        baseline_ref: str | None = None,
        step_hook: Callable[[str], None] | None = None,
    ) -> None:
        def crash_at(step: str) -> None:
            if step == "remove_added_files":
                raise _SimulatedCrash("crash during the restore")

        _real_rollback(repo_root, ref, baseline_ref=baseline_ref, step_hook=crash_at)

    monkeypatch.setattr(proposal_seal, "rollback", crashing_rollback)


def _count_restores(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Record every restore the resumed run performs."""
    restores: list[str] = []

    def counting_rollback(
        repo_root: Path | str,
        ref: str,
        baseline_ref: str | None = None,
        step_hook: Callable[[str], None] | None = None,
    ) -> None:
        restores.append(ref)
        _real_rollback(repo_root, ref, baseline_ref=baseline_ref, step_hook=step_hook)

    monkeypatch.setattr(proposal_seal, "rollback", counting_rollback)
    return restores


def _proposal_ref() -> str:
    """Return the ref the lifecycle seals the proposal under."""
    return f"refs/ralph/proposals/{_RUN_ID}/{_ITERATION}"


def _persist(state: PipelineState, checkpoint_path: Path) -> None:
    """Persist the state through the runner's own checkpoint writer."""
    save_checkpoint_or_log(
        state,
        message="Checkpoint save failed in phase={phase}: {err}",
        path=checkpoint_path,
    )


def _resume(root: Path, bundle: PolicyBundle, checkpoint_path: Path) -> PipelineState:
    """Re-enter the resume path over the PERSISTED state.

    The state is read back from DISK, exactly as a new process reads it, and
    handed to the run loop's resume seam.
    """
    persisted = load_checkpoint(checkpoint_path)
    assert persisted is not None, "the crash left no durable state to resume from"
    return resolve_initial_state(
        config=UnifiedConfig(),
        policy_bundle=bundle,
        initial_state=persisted,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
        workspace_root=root,
    )


def _drive_to_crash(
    root: Path,
    bundle: PolicyBundle,
    checkpoint_path: Path,
    baseline: Seal,
    boundary: str,
    monkeypatch: pytest.MonkeyPatch,
) -> Seal:
    """Drive the rejection lifecycle until the crash at ``boundary``.

    The lifecycle is the production order: the run's state is persisted, the
    proposal is sealed, the rejection reduces to a rollback-pending state, and
    the rollback executor persists the durable pending marker BEFORE the
    destructive restore.
    """
    # The run recorded its run-start baseline at startup, so the restore
    # point is the baseline and not ``HEAD``.
    state = PipelineState(
        phase=_COMMIT_REVIEW_PHASE,
        commit_review_rejections=0,
        rollback_baseline_ref=baseline.ref,
    )
    _persist(state, checkpoint_path)

    seal = seal_pending(root, _RUN_ID, _ITERATION)
    if boundary == "after_seal":
        raise _SimulatedCrash("crash after the proposal seal")

    pending = _pending_state(bundle, state, seal.ref)
    if boundary == "after_pending_checkpoint":
        _crash_before_restore(monkeypatch)
    elif boundary == "during_restore":
        _crash_during_restore(monkeypatch)

    cleared = execute_pending_rollback(
        pending,
        root,
        bundle.pipeline,
    )
    assert cleared.rollback_pending is False
    assert cleared.rollback_ref is None

    if boundary == "after_clear":
        raise _SimulatedCrash("crash after the durable clear")
    return seal


def _continue_review_after_crash(
    root: Path,
    bundle: PolicyBundle,
    checkpoint_path: Path,
    resumed: PipelineState,
) -> PipelineState:
    """Continue the commit review a resumed run re-enters.

    A crash after the seal leaves the run in the commit-review phase with the
    proposal sealed but NOT rejected: the resumed run re-enters the review,
    seals the same pending state again and rejects it again.
    """
    reseal = seal_pending(root, _RUN_ID, _ITERATION)
    rejected = _pending_state(bundle, resumed, reseal.ref)
    return execute_pending_rollback(
        rejected,
        root,
        bundle.pipeline,
    )


def _assert_same_end_state(
    root: Path,
    checkpoint_path: Path,
    readme: Path,
    ignored_log: Path,
    agent_state: Path,
    baseline: Seal,
    proposal_ref: str,
    added: Path,
) -> None:
    """Assert the end state every crash boundary converges on."""
    # The tree is back at the run-start baseline: the modified tracked file
    # is restored and the file the proposal added is gone.
    assert readme.read_text(encoding="utf-8") == "test"
    assert not added.exists()
    # The user's uncommitted work survived the rollback.
    user_file = root / "user_uncommitted.txt"
    assert user_file.read_text(encoding="utf-8") == "user work that must survive\n"
    # The ignored file and the .agent/ runtime state are intact.
    assert ignored_log.read_text(encoding="utf-8") == "runtime log line\n"
    assert agent_state.read_text(encoding="utf-8") == '{"phase": "development"}\n'
    # The proposal ref still exists and resolves: the rejected work stays
    # recoverable by hand, and it still carries the rejected content.
    preflight(root, proposal_ref)
    rejected_paths = {entry.path for entry in diff_tree(root, proposal_ref, base=baseline.ref)}
    assert rejected_paths == {"README.md", "rejected_added.txt"}
    # The baseline still exists and resolves.
    preflight(root, baseline.ref)
    # The durable state says: no rollback is pending any more.
    persisted = load_checkpoint(checkpoint_path)
    assert persisted is not None
    assert persisted.rollback_pending is False
    assert persisted.rollback_ref is None


@pytest.mark.parametrize("boundary", sorted(_CRASH_BOUNDARIES))
def test_a_crash_at_every_lifecycle_boundary_resumes_to_the_same_end_state(
    boundary: str,
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A crash at any lifecycle boundary converges on the same end state."""
    root = Path(tmp_git_repo)
    bundle = _bundle()
    checkpoint_path = rollback_checkpoint_path_for_root(root)

    readme, ignored_log, agent_state = _prepare_workspace(root)
    baseline = _seal_run_start_baseline(root)
    added = _apply_rejected_proposal(root, readme)

    # The crash: the process dies at the boundary under test. The seal the
    # lifecycle created is the durable evidence of the rejected work, so it
    # is captured even when the crash lands right after it.
    with pytest.raises(_SimulatedCrash):
        _drive_to_crash(root, bundle, checkpoint_path, baseline, boundary, monkeypatch)
    proposal_ref = _proposal_ref()

    # The durable recovery evidence survived the crash: the restore deletes
    # every file the proposal added, so the marker must live where the
    # restore cannot reach it.
    assert checkpoint_path.exists()
    # The proposal ref survives every crash point and still resolves.
    preflight(root, proposal_ref)

    # The resumed run reads the persisted state from DISK and re-enters the
    # resume path.
    restores = _count_restores(monkeypatch)
    resumed = _resume(root, bundle, checkpoint_path)

    if boundary == "after_seal":
        # Nothing was pending yet: the resumed run continues the review, which
        # rejects the proposal again and rolls it back.
        resumed = _continue_review_after_crash(root, bundle, checkpoint_path, resumed)
        resumed = _resume(root, bundle, checkpoint_path)

    # The restore the resumed run still owed is completed exactly once: the
    # durable clear is persisted in the checkpoint the resume reads, so no
    # later resume restores again.
    assert len(restores) == _CRASH_BOUNDARIES[boundary]
    assert resumed.rollback_pending is False
    assert resumed.rollback_ref is None

    # A SECOND resume over the same persisted state is a no-op.
    again = _resume(root, bundle, checkpoint_path)
    assert len(restores) == _CRASH_BOUNDARIES[boundary]
    assert again.rollback_pending is False
    assert again.rollback_ref is None

    _assert_same_end_state(
        root,
        checkpoint_path,
        readme,
        ignored_log,
        agent_state,
        baseline,
        proposal_ref,
        added,
    )


def test_cleared_checkpoint_persistence_failure_fails_closed_and_prevents_replay(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """When persisting the cleared rollback marker fails, the gate fails closed.

    If persisting the cleared marker after restoration fails, the runner must
    not return an in-memory cleared state that allows normal continuation
    (e.g. into development) while the on-disk state is inconsistent. Instead,
    it must route to gate failure (fail-closed) so subsequent crash/resume
    cannot replay restoration over later work.
    """
    root = Path(tmp_git_repo)
    bundle = _bundle()
    checkpoint_path = rollback_checkpoint_path_for_root(root)

    readme, ignored_log, agent_state = _prepare_workspace(root)
    baseline = _seal_run_start_baseline(root)
    added = _apply_rejected_proposal(root, readme)

    state = PipelineState(
        phase=_COMMIT_REVIEW_PHASE,
        commit_review_rejections=0,
        rollback_baseline_ref=baseline.ref,
    )
    _persist(state, checkpoint_path)

    seal = seal_pending(root, _RUN_ID, _ITERATION)
    pending = _pending_state(bundle, state, seal.ref)

    restores = _count_restores(monkeypatch)

    # Inject failure specifically when persisting the cleared state.
    # The pre-rollback pending marker save succeeds; the post-rollback cleared marker save fails.
    from ralph.pipeline import checkpoint as ckpt_module

    real_save = ckpt_module.save

    def failing_save(save_state: PipelineState, path: Path) -> None:
        if save_state.rollback_pending is False and save_state.phase != "failed_terminal":
            raise OSError("Injected I/O failure while persisting cleared rollback marker")
        real_save(save_state, path)

    monkeypatch.setattr(ckpt_module, "save", failing_save)

    failed_state = execute_pending_rollback(
        pending,
        root,
        bundle.pipeline,
        checkpoint_path=checkpoint_path,
    )

    # 1. Gate failure: fails closed, not allowing normal continuation into development.
    assert failed_state.phase == "failed_terminal"
    assert failed_state.rollback_pending is False
    assert failed_state.rollback_ref == seal.ref
    assert "gate failed" in (failed_state.last_error or "").lower()
    assert "cleared rollback marker was not persisted" in (failed_state.last_error or "").lower()

    # The restore itself ran exactly once.
    assert len(restores) == 1
    # Working tree was restored to baseline.
    assert readme.read_text(encoding="utf-8") == "test"
    assert not added.exists()
    assert ignored_log.read_text(encoding="utf-8") == "runtime log line\n"
    assert agent_state.read_text(encoding="utf-8") == '{"phase": "development"}\n'

    # 2. Persisting the fail-closed terminal state (as runner step finalization does).
    _persist(failed_state, checkpoint_path)

    # Subsequent developer / operator work in the workspace must survive without replay.
    work_file = root / "followup_work.txt"
    work_file.write_text("developer follow-up work\n", encoding="utf-8")

    # 3. Crash / resume verification: resume must not replay restoration.
    resumed = _resume(root, bundle, checkpoint_path)
    assert resumed.phase == "failed_terminal"
    assert resumed.rollback_pending is False
    assert len(restores) == 1, "Resume must not replay rollback after cleared checkpoint failure"
    assert work_file.read_text(encoding="utf-8") == "developer follow-up work\n"

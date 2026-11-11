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

_COMMIT_REVIEW_PHASE = "development_commit_review"

_RUN_ID = "run-rollback-resume"

_ITERATION = 1

_CRASH_BOUNDARIES: dict[str, int] = {
    "after_seal": 1,
    "after_pending_checkpoint": 1,
    "during_restore": 1,
    "after_clear": 0,
}

_real_rollback = proposal_seal.rollback


class _SimulatedCrash(BaseException):
    pass


@functools.cache
def _bundle() -> PolicyBundle:
    from ralph.policy.loader import load_policy

    return load_policy(_DEFAULT_POLICY_DIR)


def _prepare_workspace(root: Path) -> tuple[Path, Path, Path]:
    readme = root / "README.md"
    ignored_log = root / "activity.log"
    agent_state = root / ".agent" / "state.json"
    agent_state.parent.mkdir(parents=True, exist_ok=True)
    agent_state.write_text('{"phase": "development"}\n', encoding="utf-8")
    ignored_log.write_text("runtime log line\n", encoding="utf-8")
    exclude = root / ".git" / "info" / "exclude"
    exclude.write_text(exclude.read_text(encoding="utf-8") + "*.log\n", encoding="utf-8")
    return readme, ignored_log, agent_state


def _seal_run_start_baseline(root: Path) -> Seal:
    user_file = root / "user_uncommitted.txt"
    user_file.write_text("user work that must survive\n", encoding="utf-8")
    baseline = seal_baseline(root, _RUN_ID)
    assert baseline is not None
    return baseline


def _apply_rejected_proposal(root: Path, readme: Path) -> Path:
    readme.write_text("developer change\n", encoding="utf-8")
    added = root / "rejected_added.txt"
    added.write_text("rejected addition\n", encoding="utf-8")
    return added


def _pending_state(bundle: PolicyBundle, state: PipelineState, ref: str) -> PipelineState:
    pending, _ = reduce(
        state,
        CommitReviewRejected(reason="rejected proposal", ref=ref, hard=True),
        bundle.pipeline,
    )
    assert pending.rollback_pending is True
    assert pending.rollback_ref == ref
    return pending


def _crash_before_restore(monkeypatch: pytest.MonkeyPatch) -> None:

    def crashing_rollback(*_args: object, **_kwargs: object) -> None:
        raise _SimulatedCrash("crash after the durable pending checkpoint")

    monkeypatch.setattr(proposal_seal, "rollback", crashing_rollback)


def _crash_during_restore(monkeypatch: pytest.MonkeyPatch) -> None:

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
    return f"refs/ralph/proposals/{_RUN_ID}/{_ITERATION}"


def _persist(state: PipelineState, checkpoint_path: Path) -> None:
    save_checkpoint_or_log(
        state,
        message="Checkpoint save failed in phase={phase}: {err}",
        path=checkpoint_path,
    )


def _resume(root: Path, bundle: PolicyBundle, checkpoint_path: Path) -> PipelineState:
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
    assert readme.read_text(encoding="utf-8") == "test"
    assert not added.exists()
    user_file = root / "user_uncommitted.txt"
    assert user_file.read_text(encoding="utf-8") == "user work that must survive\n"
    assert ignored_log.read_text(encoding="utf-8") == "runtime log line\n"
    assert agent_state.read_text(encoding="utf-8") == '{"phase": "development"}\n'
    preflight(root, proposal_ref)
    rejected_paths = {entry.path for entry in diff_tree(root, proposal_ref, base=baseline.ref)}
    assert rejected_paths == {"README.md", "rejected_added.txt"}
    preflight(root, baseline.ref)
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
    root = Path(tmp_git_repo)
    bundle = _bundle()
    checkpoint_path = rollback_checkpoint_path_for_root(root)

    readme, ignored_log, agent_state = _prepare_workspace(root)
    baseline = _seal_run_start_baseline(root)
    added = _apply_rejected_proposal(root, readme)

    with pytest.raises(_SimulatedCrash):
        _drive_to_crash(root, bundle, checkpoint_path, baseline, boundary, monkeypatch)
    proposal_ref = _proposal_ref()

    assert checkpoint_path.exists()
    preflight(root, proposal_ref)

    restores = _count_restores(monkeypatch)
    resumed = _resume(root, bundle, checkpoint_path)

    if boundary == "after_seal":
        resumed = _continue_review_after_crash(root, bundle, checkpoint_path, resumed)
        resumed = _resume(root, bundle, checkpoint_path)

    assert len(restores) == _CRASH_BOUNDARIES[boundary]
    assert resumed.rollback_pending is False
    assert resumed.rollback_ref is None

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

    assert failed_state.phase == "failed_terminal"
    assert failed_state.rollback_pending is False
    assert failed_state.rollback_ref == seal.ref
    assert "gate failed" in (failed_state.last_error or "").lower()
    assert "cleared rollback marker was not persisted" in (failed_state.last_error or "").lower()

    assert len(restores) == 1
    assert readme.read_text(encoding="utf-8") == "test"
    assert not added.exists()
    assert ignored_log.read_text(encoding="utf-8") == "runtime log line\n"
    assert agent_state.read_text(encoding="utf-8") == '{"phase": "development"}\n'

    _persist(failed_state, checkpoint_path)

    work_file = root / "followup_work.txt"
    work_file.write_text("developer follow-up work\n", encoding="utf-8")

    resumed = _resume(root, bundle, checkpoint_path)
    assert resumed.phase == "failed_terminal"
    assert resumed.rollback_pending is False
    assert len(restores) == 1, "Resume must not replay rollback after cleared checkpoint failure"
    assert work_file.read_text(encoding="utf-8") == "developer follow-up work\n"

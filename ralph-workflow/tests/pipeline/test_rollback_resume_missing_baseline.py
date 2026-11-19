from __future__ import annotations

import json
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.git.proposal_seal import baseline_is_restorable, rollback, seal_baseline, seal_pending
from ralph.pipeline.checkpoint import load as load_checkpoint
from ralph.pipeline.runner import rollback_checkpoint_path_for_root
from ralph.pipeline.state import PipelineState
from tests.pipeline.test_commit_review_rollback_resume import (
    _apply_rejected_proposal,
    _bundle,
    _pending_state,
    _persist,
    _prepare_workspace,
    _resume,
)

if TYPE_CHECKING:
    from ralph.git.proposal_seal import Seal

_COMMIT_REVIEW_PHASE = "development_commit_review"
_FAILED_TERMINAL = "failed_terminal"
_LEGACY_RUN_ID = "0c5898f27c43402dae792058b0dc9737"
_USER_WORK = "user work that must survive\n"


def _loose_ref(root: Path, ref: str) -> Path:
    path = root / ".git" / ref
    assert path.is_file(), ref
    return path


def _pending_rollback_checkpoint(root: Path, *, drop: tuple[str, ...]) -> tuple[Path, Seal]:
    bundle = _bundle()
    checkpoint_path = rollback_checkpoint_path_for_root(root)
    readme, _ignored_log, _agent_state = _prepare_workspace(root)
    user_file = root / "user_uncommitted.txt"
    user_file.write_text(_USER_WORK, encoding="utf-8")
    baseline = seal_baseline(root, _LEGACY_RUN_ID)
    assert baseline is not None
    for ref in drop:
        _loose_ref(root, ref).unlink()
    _apply_rejected_proposal(root, readme)
    state = PipelineState(
        phase=_COMMIT_REVIEW_PHASE,
        commit_review_rejections=0,
        rollback_baseline_ref=baseline.ref,
    )
    seal = seal_pending(root, _LEGACY_RUN_ID, 1)
    _persist(_pending_state(bundle, state, seal.ref), checkpoint_path)
    return checkpoint_path, baseline


def _assert_resumed_without_reset(root: Path, resumed: PipelineState, stale: str) -> None:
    assert resumed.phase != _FAILED_TERMINAL
    assert resumed.rollback_pending is False
    assert resumed.rollback_ref is None
    user_file = root / "user_uncommitted.txt"
    readme = root / "README.md"
    assert user_file.read_text(encoding="utf-8") == _USER_WORK
    assert readme.read_text(encoding="utf-8") == "developer change\n"
    assert (root / "rejected_added.txt").exists()
    fresh = resumed.rollback_baseline_ref
    assert fresh is not None
    assert fresh != stale
    assert baseline_is_restorable(root, fresh)


def test_a_resume_whose_baseline_predates_the_index_ref_keeps_the_tree_and_continues(
    tmp_git_repo: Path,
) -> None:
    root = Path(tmp_git_repo)
    index_ref = f"refs/ralph/baseline-index/{_LEGACY_RUN_ID}"
    checkpoint_path, baseline = _pending_rollback_checkpoint(root, drop=(index_ref,))

    resumed = _resume(root, _bundle(), checkpoint_path)

    _assert_resumed_without_reset(root, resumed, baseline.ref)
    persisted = load_checkpoint(checkpoint_path)
    assert persisted is not None
    assert persisted.rollback_pending is False


def test_an_in_run_rollback_to_a_baseline_without_an_index_ref_restores_the_baseline(
    tmp_git_repo: Path,
) -> None:
    root = Path(tmp_git_repo)
    index_ref = f"refs/ralph/baseline-index/{_LEGACY_RUN_ID}"
    _checkpoint_path, baseline = _pending_rollback_checkpoint(root, drop=(index_ref,))

    rollback(root, f"refs/ralph/proposals/{_LEGACY_RUN_ID}/1", baseline_ref=baseline.ref)

    readme = root / "README.md"
    user_file = root / "user_uncommitted.txt"
    assert readme.read_text(encoding="utf-8") == "test"
    assert not (root / "rejected_added.txt").exists()
    assert user_file.read_text(encoding="utf-8") == _USER_WORK


def test_a_resume_whose_baseline_ref_is_gone_keeps_the_worktree_and_continues(
    tmp_git_repo: Path,
) -> None:
    root = Path(tmp_git_repo)
    dropped = (
        f"refs/ralph/baseline-index/{_LEGACY_RUN_ID}",
        f"refs/ralph/baselines/{_LEGACY_RUN_ID}",
    )
    checkpoint_path, _baseline = _pending_rollback_checkpoint(root, drop=dropped)

    resumed = _resume(root, _bundle(), checkpoint_path)

    _assert_resumed_without_reset(root, resumed, dropped[1])


def test_an_old_checkpoint_without_run_or_baseline_fields_resumes(tmp_git_repo: Path) -> None:
    root = Path(tmp_git_repo)
    checkpoint_path = rollback_checkpoint_path_for_root(root)
    _persist(PipelineState(phase=_COMMIT_REVIEW_PHASE), checkpoint_path)
    raw = json.loads(checkpoint_path.read_text(encoding="utf-8"))
    for field in (
        "run_id",
        "rollback_pending",
        "rollback_ref",
        "rollback_baseline_ref",
        "rollback_baseline_index_ref",
        "agent_cooldowns",
    ):
        raw.pop(field, None)
    checkpoint_path.write_text(json.dumps(raw), encoding="utf-8")

    resumed = _resume(root, _bundle(), checkpoint_path)

    assert resumed.phase == _COMMIT_REVIEW_PHASE
    assert resumed.rollback_pending is False

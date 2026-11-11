from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.config.models import UnifiedConfig
from ralph.git.proposal_seal import ProposalPreflightError, preflight, seal_pending
from ralph.phases.commit_review import CommitReviewRejected
from ralph.pipeline.reducer import reduce
from ralph.pipeline.run_loop import resolve_initial_state
from ralph.pipeline.runner import execute_pending_rollback
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PolicyBundle

pytestmark = [pytest.mark.timeout_seconds(10), pytest.mark.required_auto_integrate_e2e]

_DEFAULTS_DIR = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"

_DEVELOPER_CHANGE = "developer change\n"

_REJECTED_ADDITION = "rejected addition\n"


def _bundle() -> PolicyBundle:
    return load_policy(_DEFAULTS_DIR)


def _rejected_state(repo_root: Path, run_id: str) -> PipelineState:
    readme = repo_root / "README.md"
    readme.write_text(_DEVELOPER_CHANGE, encoding="utf-8")
    (repo_root / "rejected-only.txt").write_text(_REJECTED_ADDITION, encoding="utf-8")
    seal = seal_pending(repo_root, run_id=run_id, iteration="0")
    rejected_state, _ = reduce(
        PipelineState(phase="development_commit_review"),
        CommitReviewRejected(reason="rejected proposal", ref=seal.ref, hard=True),
        _bundle().pipeline,
    )
    assert rejected_state.rollback_pending is True
    assert rejected_state.rollback_ref == seal.ref
    return rejected_state


def test_checkpoint_failure_refuses_rollback_and_keeps_rejected_work(
    tmp_git_repo: Path,
) -> None:
    repo_root = Path(tmp_git_repo)
    rejected_state = _rejected_state(repo_root, run_id="run-checkpoint-failure")
    checkpoint_path = repo_root / "checkpoint.json"
    checkpoint_path.mkdir()

    result = execute_pending_rollback(
        rejected_state,
        repo_root,
        _bundle().pipeline,
        checkpoint_path=checkpoint_path,
    )

    assert (repo_root / "README.md").read_text(encoding="utf-8") == _DEVELOPER_CHANGE
    assert (repo_root / "rejected-only.txt").read_text(encoding="utf-8") == _REJECTED_ADDITION
    assert rejected_state.rollback_ref is not None
    preflight(repo_root, rejected_state.rollback_ref)
    assert result.rollback_ref == rejected_state.rollback_ref
    assert result.phase == "failed_terminal"
    assert result.rollback_pending is False
    assert "gate failed" in (result.last_error or "")
    assert "checkpoint" in (result.last_error or "").lower()


def _damage_git(repo_root: Path) -> None:
    head_path = repo_root / ".git" / "HEAD"
    head_path.write_text("ref: refs/heads/gone\n", encoding="utf-8")


def _proposal_ref_file(repo_root: Path, run_id: str) -> Path:
    return repo_root / ".git" / "refs" / "ralph" / "proposals" / run_id / "0"


def test_damaged_git_is_a_gate_failure_and_never_a_rollback(tmp_git_repo: Path) -> None:
    repo_root = Path(tmp_git_repo)
    rejected_state = _rejected_state(repo_root, run_id="run-damaged")
    _damage_git(repo_root)

    result = execute_pending_rollback(rejected_state, repo_root, _bundle().pipeline)

    assert result.phase == "failed_terminal"
    assert result.rollback_pending is False
    assert "gate failed" in (result.last_error or "")
    readme = repo_root / "README.md"
    assert readme.read_text(encoding="utf-8") == _DEVELOPER_CHANGE
    proposal_file = _proposal_ref_file(repo_root, "run-damaged")
    assert proposal_file.exists()


def test_a_damaged_git_stops_the_resumed_run_the_same_way(tmp_git_repo: Path) -> None:
    repo_root = Path(tmp_git_repo)
    rejected_state = _rejected_state(repo_root, run_id="run-damaged-resume")
    _damage_git(repo_root)

    resumed = resolve_initial_state(
        config=UnifiedConfig(),
        policy_bundle=_bundle(),
        initial_state=rejected_state,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
        workspace_root=repo_root,
    )

    assert resumed.phase == "failed_terminal"
    assert resumed.rollback_pending is False
    readme = repo_root / "README.md"
    assert readme.read_text(encoding="utf-8") == _DEVELOPER_CHANGE
    proposal_file = _proposal_ref_file(repo_root, "run-damaged-resume")
    assert proposal_file.exists()


def test_an_unresolvable_proposal_ref_is_a_gate_failure_not_a_rollback(
    tmp_git_repo: Path,
) -> None:
    repo_root = Path(tmp_git_repo)
    rejected_state = _rejected_state(repo_root, run_id="run-missing-proposal")
    assert rejected_state.rollback_ref is not None

    _proposal_ref_file(repo_root, "run-missing-proposal").unlink()
    with pytest.raises(ProposalPreflightError):
        preflight(repo_root, rejected_state.rollback_ref)

    result = execute_pending_rollback(rejected_state, repo_root, _bundle().pipeline)

    assert result.phase == "failed_terminal"
    assert result.rollback_pending is False
    assert "gate failed" in (result.last_error or "")
    readme = repo_root / "README.md"
    assert readme.read_text(encoding="utf-8") == _DEVELOPER_CHANGE


def test_an_unresolvable_baseline_is_a_gate_failure_not_a_rollback(tmp_git_repo: Path) -> None:
    repo_root = Path(tmp_git_repo)
    rejected_state = _rejected_state(repo_root, run_id="run-missing-baseline")
    rejected_state = rejected_state.copy_with(
        rollback_baseline_ref="refs/ralph/baselines/does-not-exist"
    )

    result = execute_pending_rollback(rejected_state, repo_root, _bundle().pipeline)

    assert result.phase == "failed_terminal"
    assert result.rollback_pending is False
    readme = repo_root / "README.md"
    assert readme.read_text(encoding="utf-8") == _DEVELOPER_CHANGE

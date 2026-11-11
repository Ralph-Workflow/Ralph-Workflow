"""End-to-end proof that a damaged ``.git`` is a gate failure, not a rollback.

A rejection restores the tree only when the restore can be preflighted.
When the repository metadata is damaged -- ``HEAD`` no longer resolves --
the preflight fails, so the pipeline must NOT attempt a rollback: the
working tree keeps exactly the state it had, the proposal ref is kept so
the rejected work stays recoverable, and the run stops on the policy's
failure route instead of restoring blindly.

The proof drives the public seams -- the pipeline-level rollback executor
(``ralph.pipeline.runner.execute_pending_rollback``) and the run loop's
resume path (``ralph.pipeline.run_loop.resolve_initial_state``) -- against
real temporary Git repositories: no sleeps, no network, and no mutation of
any repository outside its temporary directory.
"""

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

#: The content the rejected iteration left in the tracked file.
_DEVELOPER_CHANGE = "developer change\n"


def _bundle() -> PolicyBundle:
    """Return the bundled default policy."""
    return load_policy(_DEFAULTS_DIR)


def _rejected_state(repo_root: Path, run_id: str) -> PipelineState:
    """Seal the pending state, reject it, and return the rollback-pending state."""
    readme = repo_root / "README.md"
    readme.write_text(_DEVELOPER_CHANGE, encoding="utf-8")
    seal = seal_pending(repo_root, run_id=run_id, iteration="0")
    rejected_state, _ = reduce(
        PipelineState(phase="development_commit_review"),
        CommitReviewRejected(reason="rejected proposal", ref=seal.ref, hard=True),
        _bundle().pipeline,
    )
    assert rejected_state.rollback_pending is True
    assert rejected_state.rollback_ref == seal.ref
    return rejected_state


def _damage_git(repo_root: Path) -> None:
    """Damage the repository metadata so ``HEAD`` no longer resolves.

    The proposal ref stays on disk: only ``HEAD`` is broken, so the proof
    can show that a failed preflight keeps the rejected work recoverable.
    """
    head_path = repo_root / ".git" / "HEAD"
    head_path.write_text("ref: refs/heads/gone\n", encoding="utf-8")


def _proposal_ref_file(repo_root: Path, run_id: str) -> Path:
    """Return the loose-ref file holding the rejected iteration's proposal."""
    return repo_root / ".git" / "refs" / "ralph" / "proposals" / run_id / "0"


def test_damaged_git_is_a_gate_failure_and_never_a_rollback(tmp_git_repo: Path) -> None:
    """A rollback that cannot be preflighted stops the run and touches nothing."""
    repo_root = Path(tmp_git_repo)
    rejected_state = _rejected_state(repo_root, run_id="run-damaged")
    _damage_git(repo_root)

    result = execute_pending_rollback(rejected_state, repo_root, _bundle().pipeline)

    # The run stopped on the policy's failure route instead of rolling back.
    assert result.phase == "failed_terminal"
    assert result.rollback_pending is False
    assert "gate failed" in (result.last_error or "")
    # The tree is untouched: the rejected iteration's change is still there.
    readme = repo_root / "README.md"
    assert readme.read_text(encoding="utf-8") == _DEVELOPER_CHANGE
    # The proposal ref is kept, so the rejected work stays recoverable.
    proposal_file = _proposal_ref_file(repo_root, "run-damaged")
    assert proposal_file.exists()


def test_a_damaged_git_stops_the_resumed_run_the_same_way(tmp_git_repo: Path) -> None:
    """The run-loop resume seam fails closed the same way."""
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
    """A rejected proposal whose ref does not resolve stops the run, untouched.

    The rejection is only actionable when the proposal ref resolves: the ref
    is what makes the rejected work recoverable and what the preflight
    confirms before any restore. When it cannot be resolved the run must
    fail the gate instead of restoring blindly.
    """
    repo_root = Path(tmp_git_repo)
    rejected_state = _rejected_state(repo_root, run_id="run-missing-proposal")
    assert rejected_state.rollback_ref is not None

    # The proposal ref is gone: the rejected work is no longer recoverable.
    _proposal_ref_file(repo_root, "run-missing-proposal").unlink()
    with pytest.raises(ProposalPreflightError):
        preflight(repo_root, rejected_state.rollback_ref)

    result = execute_pending_rollback(rejected_state, repo_root, _bundle().pipeline)

    # The run stopped on the policy's failure route instead of rolling back.
    assert result.phase == "failed_terminal"
    assert result.rollback_pending is False
    assert "gate failed" in (result.last_error or "")
    # The tree is untouched: the restore never started.
    readme = repo_root / "README.md"
    assert readme.read_text(encoding="utf-8") == _DEVELOPER_CHANGE


def test_an_unresolvable_baseline_is_a_gate_failure_not_a_rollback(tmp_git_repo: Path) -> None:
    """A recorded baseline that does not resolve stops the run, untouched."""
    repo_root = Path(tmp_git_repo)
    rejected_state = _rejected_state(repo_root, run_id="run-missing-baseline")
    rejected_state = rejected_state.copy_with(
        rollback_baseline_ref="refs/ralph/baselines/does-not-exist"
    )

    result = execute_pending_rollback(rejected_state, repo_root, _bundle().pipeline)

    assert result.phase == "failed_terminal"
    assert result.rollback_pending is False
    # The tree is untouched: the restore to the baseline never started.
    readme = repo_root / "README.md"
    assert readme.read_text(encoding="utf-8") == _DEVELOPER_CHANGE

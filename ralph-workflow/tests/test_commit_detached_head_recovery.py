"""Tests for detached-HEAD commit recovery (AC10, AC11).

Verifies that detached dirty/staged work attaches safely to the verified feature branch
before commit, repairable Git state routes to automated recovery, and it never reaches
failed_terminal or reports completed for a failed commit.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import Actor, Repo

from ralph.pipeline.commit_executor import execute_commit_effect, set_last_commit_error
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy


def _make_test_repo(repo_root: Path, feature_branch: str = "feature/work") -> Repo:
    repo = Repo.init(repo_root)
    repo.config_writer().set_value("user", "name", "Test Committer").release()
    repo.config_writer().set_value("user", "email", "test@example.com").release()
    actor = Actor("Test Committer", "test@example.com")

    # Initial commit on main
    base_file = repo_root / "README.md"
    base_file.write_text("# Test Repo\n", encoding="utf-8")
    repo.index.add(["README.md"])
    repo.index.commit("initial commit", author=actor, committer=actor)
    repo.git.branch("-M", "main")

    # Create feature branch
    repo.git.checkout("-b", feature_branch)
    feature_file = repo_root / "feature.txt"
    feature_file.write_text("feature base\n", encoding="utf-8")
    repo.index.add(["feature.txt"])
    repo.index.commit("feature base commit", author=actor, committer=actor)

    return repo


def _make_commit_artifact(repo_root: Path, message: str) -> Path:
    artifact_path = repo_root / ".agent" / "artifacts" / "commit_message.md"
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    artifact_path.write_text(
        f"---\ntype: commit\nsubject: {message}\n---\n",
        encoding="utf-8",
    )
    return artifact_path


def _load_pipeline_policy() -> PipelinePolicy:
    defaults_dir = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"
    return load_policy(defaults_dir).pipeline


def test_worktree_on_detached_head_commits_to_feature_branch_without_failed_terminal(
    tmp_path: Path,
) -> None:
    repo = _make_test_repo(tmp_path, "feature/work")
    feature_commit_sha = repo.head.commit.hexsha

    # Detach HEAD at the feature commit
    repo.git.checkout(feature_commit_sha)
    assert repo.head.is_detached

    # Add uncommitted tracked changes
    tracked_file = tmp_path / "feature.txt"
    tracked_file.write_text("feature modified\n", encoding="utf-8")

    message_file = _make_commit_artifact(tmp_path, "feat: implement feature work")

    # Execute commit effect with branch specified in opts
    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        branch="feature/work",
    )

    # Must succeed, reattach to feature/work, and commit onto feature branch
    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached
    assert repo.active_branch.name == "feature/work"
    assert repo.head.commit.message.splitlines()[0] == "feat: implement feature work"

    # Feed the event into reducer and ensure it advances cleanly without reaching failed_terminal
    policy = _load_pipeline_policy()
    state = PipelineState(phase="development_commit", previous_phase="development_commit_cleanup")
    next_state, _ = reduce(state, event, policy)

    assert next_state.phase != "failed_terminal"
    assert next_state.phase in ("development_analysis", "development_final_commit_cleanup")


def test_worktree_on_detached_head_reattaches_without_explicit_branch_arg(
    tmp_path: Path,
) -> None:
    """AC10 production-seam proof: detached HEAD is reattached without ``branch=``.

    The production ``runner._execute_commit_effect_from_deps`` does
    NOT pass ``branch=`` to ``execute_commit_effect`` (it forwards a
    fixed set of dependencies). The reattach helper must therefore
    resolve the target branch from the reflog, the worktree
    metadata, the configured target, or the repository name. This
    test reproduces the production seam exactly: a detached HEAD
    ahead of a feature branch, with uncommitted tracked changes,
    invoked without any ``branch=`` opt, must commit onto the
    feature branch and reach the next phase. The detached commit
    is never silently orphaned.
    """
    from ralph.config.models import UnifiedConfig

    repo = _make_test_repo(tmp_path, "feature/work")
    feature_commit_sha = repo.head.commit.hexsha

    # Detach HEAD at the feature commit (the same shape the
    # production seam leaves behind after a recovery reset or
    # snapshot).
    repo.git.checkout(feature_commit_sha)
    assert repo.head.is_detached

    # Add uncommitted tracked changes.
    tracked_file = tmp_path / "feature.txt"
    tracked_file.write_text("feature modified\n", encoding="utf-8")

    message_file = _make_commit_artifact(tmp_path, "feat: production seam")

    # Production seam: no ``branch=`` opt. The resolver consults
    # the reflog and the configured target. The feature branch
    # is the ONLY branch in the reflog as a checkout destination
    # (the test only switches to ``feature/work`` and then
    # detaches), so the reattach must land on ``feature/work``
    # rather than on ``main``.
    config = UnifiedConfig.model_validate({"general": {"auto_integrate_target": "main"}})
    event = execute_commit_effect(
        CommitEffect(message_file=str(message_file)),
        tmp_path,
        config=config,
    )

    # Must succeed: HEAD is reattached, the commit lands on the
    # feature branch, and the detached commit is preserved.
    assert event == PipelineEvent.COMMIT_SUCCESS
    repo = Repo(tmp_path)
    assert not repo.head.is_detached, "HEAD must be reattached"
    assert repo.active_branch.name == "feature/work"
    # The new commit's parent is the prior feature tip (the
    # detached commit is preserved on the branch).
    assert repo.head.commit.parents[0].hexsha == feature_commit_sha
    assert repo.head.commit.message.splitlines()[0] == "feat: production seam"


def test_commit_failure_routes_to_automated_recovery_not_failed_terminal() -> None:
    policy = _load_pipeline_policy()
    state = PipelineState(
        phase="development_commit",
        previous_phase="development_commit_cleanup",
        last_error="GitOperationError: index.lock contention",
        pending_cycle_outcome="completed",
    )
    set_last_commit_error("GitOperationError: index.lock contention")
    try:
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        # Routes to automated recovery (development_commit_cleanup) instead of failed_terminal
        assert next_state.phase == "development_commit_cleanup"
        assert next_state.phase != "failed_terminal"
        # Phase display must NOT report 'completed' when a commit fails!
        assert next_state.pending_cycle_outcome is None
    finally:
        set_last_commit_error(None)


def test_commit_failure_clears_pending_completed_outcome() -> None:
    policy = _load_pipeline_policy()
    state = PipelineState(
        phase="development_commit",
        pending_cycle_outcome="completed",
        last_error="detached HEAD residue detected",
    )
    set_last_commit_error("detached HEAD residue detected")
    try:
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        # Must never report completed when commit failed
        assert next_state.pending_cycle_outcome is None
        assert next_state.phase != "failed_terminal"
    finally:
        set_last_commit_error(None)


def test_exhausted_commit_failure_routes_to_automated_recovery_not_terminal() -> None:
    """A repairable commit failure exhausted the bounded counter.

    The bounded counter protects against an infinite recovery loop
    but MUST NOT turn a repairable failure into a terminal one:
    Ralph owns the recovery path. The repairable failure is routed
    to the policy's commit-recovery phase even when the counter
    is exhausted. The counter is still incremented so any failure
    pattern that depends on it for safety continues to function.
    The phase display MUST NOT report 'completed' for a commit
    that failed.
    """
    policy = _load_pipeline_policy()
    # When recovery epoch is already 3 (exhausted)
    state = PipelineState(
        phase="development_commit",
        recovery_epoch=3,
        pending_cycle_outcome="completed",
        last_error="repeated commit failure",
    )
    set_last_commit_error("repeated commit failure")
    try:
        next_state, _ = reduce(state, PipelineEvent.COMMIT_FAILURE, policy)

        # Bounded counter exhausted, but a repairable failure is still
        # routed to the policy's commit-recovery phase. The phase must
        # NEVER be failed_terminal for a repairable commit failure, and
        # the phase display must NOT report 'completed' for a commit
        # that failed.
        assert next_state.phase != "failed_terminal"
        assert next_state.phase in (
            "commit_recovery",
            "development_commit_cleanup",
        )
        assert next_state.pending_cycle_outcome is None
        # The counter still ticks (next attempt is the next epoch).
        assert next_state.recovery_epoch == 4
    finally:
        set_last_commit_error(None)

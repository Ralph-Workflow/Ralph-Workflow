"""End-to-end integration tests for commit review safety gate, rollback, and resume.

The proofs drive the REAL git boundary (proposal seals, SHA-bound commit
effects, reducer-driven rollback and resume), so the file is registered in
``ralph.test_suites.REQUIRED_AUTO_INTEGRATE_E2E_FILES``: the registry
stamps every item with ``required_auto_integrate_e2e`` (the DEFAULT
marker expression selects it) and sanctions the real-Git crossings for
the test-policy audit. A module-level ``subprocess_e2e`` marker would
instead drop the file from static discovery and deselect it from the
default ``make test`` profile, so the coverage would rot unnoticed in the
opt-in lane.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest
from git import Actor, Repo

from ralph.config.bootstrap import auto_seed_default_git_exclude
from ralph.config.models import UnifiedConfig
from ralph.git.proposal_seal import ProposalPreflightError, preflight, rollback, seal_pending
from ralph.phases.commit_review import (
    CommitReviewApproved,
    CommitReviewRejected,
)
from ralph.phases.required_artifacts import read_validation_retry_hint, retry_hint_path
from ralph.pipeline.commit_executor import execute_commit_effect
from ralph.pipeline.effects import CommitEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce
from ralph.pipeline.run_loop import resolve_initial_state
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.policy.models import PipelinePolicy, PolicyBundle

pytestmark = [pytest.mark.timeout_seconds(10), pytest.mark.required_auto_integrate_e2e]

_DEFAULTS_DIR = Path(__file__).resolve().parents[2] / "ralph" / "policy" / "defaults"


def _bundle() -> PolicyBundle:
    return load_policy(_DEFAULTS_DIR)


def _policy() -> PipelinePolicy:
    return _bundle().pipeline


def _init_repo(tmp_path: Path) -> Repo:
    repo = Repo.init(tmp_path)
    repo.config_writer().set_value("user", "name", "Gate Test").release()
    repo.config_writer().set_value("user", "email", "gate@example.com").release()
    actor = Actor("Gate Test", "gate@example.com")
    tracked_file = tmp_path / "app.py"
    tracked_file.write_text("print('hello')", encoding="utf-8")
    auto_seed_default_git_exclude(tmp_path)
    repo.index.add(["app.py"])
    repo.index.commit("initial commit", author=actor, committer=actor)
    return repo


def _create_commit_message_artifact(repo_root: Path, message: str = "feat: good change") -> Path:
    artifact = repo_root / ".agent" / "artifacts" / "commit_message.md"
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(
        f"---\ntype: commit\nsubject: {message}\n---\n",
        encoding="utf-8",
    )
    return artifact


def _bundle_with_retention(retention: int) -> PolicyBundle:
    """Return the bundled default policy with ``proposal_retention`` overridden."""
    bundle = _bundle()
    commit_review = bundle.pipeline.commit_review.model_copy(
        update={"proposal_retention": retention}
    )
    pipeline = bundle.pipeline.model_copy(update={"commit_review": commit_review})
    return bundle.model_copy(update={"pipeline": pipeline})


def test_commit_review_gate_approve_creates_sha_bound_commit_and_retains_the_proposal(
    tmp_path: Path,
) -> None:
    """Approval routes to commit with SHA-bound staging and creates the commit."""
    policy = _policy()
    repo = _init_repo(tmp_path)

    # 1. Developer modifies code
    code_file = tmp_path / "app.py"
    code_file.write_text("print('hello, approved world!')", encoding="utf-8")

    # 2. Stage pending changes into a proposal seal
    seal = seal_pending(tmp_path, run_id="run-100", iteration="0")
    assert seal.tree_sha is not None
    preflight(tmp_path, seal.ref)

    # 3. Reviewer emits CommitReviewApproved
    state = PipelineState(
        phase="development_commit_review",
        commit_review_rejections=0,
    )
    event = CommitReviewApproved(tree_sha=seal.tree_sha)
    next_state, _ = reduce(state, event, policy)

    assert next_state.phase == "development_commit"
    assert next_state.approved_tree_sha == seal.tree_sha

    # 4. Commit effect is executed with expected_tree_sha
    msg_path = _create_commit_message_artifact(tmp_path, "feat: approved work")
    commit_effect = CommitEffect(
        message_file=str(msg_path),
        expected_tree_sha=next_state.approved_tree_sha,
    )
    commit_event = execute_commit_effect(
        commit_effect,
        tmp_path,
        branch=repo.active_branch.name,
    )
    assert commit_event == PipelineEvent.COMMIT_SUCCESS

    # Verify head moved to the new commit
    repo = Repo(tmp_path)
    assert repo.head.commit.message.splitlines()[0] == "feat: approved work"

    # The post-commit proposal sweep KEEPS the most recent sealed proposals:
    # ``commit_review.proposal_retention`` bounds how many refs the sweep
    # retains and the sweep never deletes a rejected iteration's ref (the
    # documented recovery is ``git checkout <ref> -- .``). This proposal's
    # tree became the commit, so it is not a rejected ref, and the bundled
    # retention budget is not exhausted: the sweep retains the ref and it
    # still resolves.
    preflight(tmp_path, seal.ref)


def test_commit_review_gate_prunes_the_proposals_outside_the_retention_bound(
    tmp_path: Path,
) -> None:
    """A successful commit prunes the proposal refs the retention bound excludes."""
    repo = _init_repo(tmp_path)
    initial_sha = repo.head.commit.hexsha

    # The developer changes the tracked file; the sealed proposal points at
    # the tree the approved commit is about to create.
    code_file = tmp_path / "app.py"
    code_file.write_text("print('hello, retained world!')", encoding="utf-8")
    seal = seal_pending(tmp_path, run_id="run-200", iteration="1")
    assert seal.tree_sha is not None

    # An older approved iteration's proposal ref points at a tree that DID
    # become a commit, so the sweep does not protect it as a rejected ref.
    older_ref = "refs/ralph/proposals/run-200/0"
    repo.git.update_ref(older_ref, initial_sha)

    msg_path = _create_commit_message_artifact(tmp_path, "feat: bounded sweep")
    commit_effect = CommitEffect(
        message_file=str(msg_path),
        expected_tree_sha=seal.tree_sha,
    )
    commit_event = execute_commit_effect(
        commit_effect,
        tmp_path,
        branch=repo.active_branch.name,
        policy_bundle=_bundle_with_retention(1),
    )
    assert commit_event == PipelineEvent.COMMIT_SUCCESS

    # The newest proposal ref is inside the retention bound and survives.
    preflight(tmp_path, seal.ref)
    # The older one is outside it and the sweep deletes it.
    with pytest.raises(ProposalPreflightError):
        preflight(tmp_path, older_ref)


def test_commit_review_gate_sha_mismatch_fails_commit(tmp_path: Path) -> None:
    """If working tree / index has changed since approval, commit effect is rejected."""
    _init_repo(tmp_path)

    code_file = tmp_path / "app.py"
    code_file.write_text("print('hello, tampered world!')", encoding="utf-8")

    msg_path = _create_commit_message_artifact(tmp_path, "feat: tampered work")
    commit_effect = CommitEffect(
        message_file=str(msg_path),
        expected_tree_sha="0123456789abcdef0123456789abcdef01234567",
    )
    commit_event = execute_commit_effect(commit_effect, tmp_path)
    assert commit_event == PipelineEvent.COMMIT_FAILURE


def test_commit_review_gate_reject_executes_rollback_and_preserves_context(tmp_path: Path) -> None:
    """Rejection rolls back working tree to HEAD, preserves proposal ref, and stores hint."""
    policy = _policy()
    repo = _init_repo(tmp_path)
    head_sha = repo.head.commit.hexsha

    # Developer creates a good change and a bad file
    bad_file = tmp_path / "corrupt_data.bin"
    bad_file.write_bytes(b"\x00\xff" * 100)
    code_file = tmp_path / "app.py"
    code_file.write_text("print('modified')", encoding="utf-8")

    # Seal proposal
    seal = seal_pending(tmp_path, run_id="run-101", iteration="0")
    preflight(tmp_path, seal.ref)

    # Reviewer emits CommitReviewRejected
    state = PipelineState(
        phase="development_commit_review",
        commit_review_rejections=0,
    )
    reject_event = CommitReviewRejected(
        reason="corrupt_data.bin contains binary payload",
        ref=seal.ref,
        hard=True,
    )
    next_state, _ = reduce(state, reject_event, policy)

    # Reducer asserts
    assert next_state.phase == "development"
    assert next_state.commit_review_rejections == 1
    assert next_state.rollback_pending is True
    assert next_state.rollback_ref == seal.ref

    # Execute rollback (simulating runner step)
    rollback(tmp_path, next_state.rollback_ref)

    # Working tree restored: bad file gone, app.py restored to initial commit
    assert not bad_file.exists()
    assert code_file.read_text(encoding="utf-8") == "print('hello')"
    repo = Repo(tmp_path)
    assert repo.head.commit.hexsha == head_sha

    # Proposal ref preserved for debugging/recovery
    preflight(tmp_path, seal.ref)

    # Store retry hint for next development prompt
    hint_file = tmp_path / retry_hint_path("development", pipeline_policy=policy)
    hint_file.parent.mkdir(parents=True, exist_ok=True)
    hint_file.write_text(
        f"Commit review rejected: {next_state.last_review_rejection_reason}\nRef: {next_state.rollback_ref}",
        encoding="utf-8",
    )

    # Verify development prompt can read the hint
    hint_content = read_validation_retry_hint(tmp_path, "development")
    assert "corrupt_data.bin contains binary payload" in hint_content
    assert seal.ref in hint_content


def test_commit_review_gate_counter_exhaustion_routes_to_failure(tmp_path: Path) -> None:
    """When rejection limit is reached, route to failed_terminal and preserve proposal ref."""
    policy = _policy()
    _init_repo(tmp_path)

    bad_file = tmp_path / "bad.txt"
    bad_file.write_text("bad", encoding="utf-8")
    seal = seal_pending(tmp_path, run_id="run-102", iteration="1")

    # State already at 2 rejections (policy default max is 2)
    state = PipelineState(
        phase="development_commit_review",
        commit_review_rejections=2,
    )
    reject_event = CommitReviewRejected(
        reason="Repeated damage after retry",
        ref=seal.ref,
    )
    next_state, _ = reduce(state, reject_event, policy)

    assert next_state.phase == policy.recovery.failed_route
    assert next_state.commit_review_rejections == 3
    assert next_state.rollback_pending is False
    assert "limit exceeded" in (next_state.last_error or "")

    # Proposal ref must still exist (preserved for inspection)
    preflight(tmp_path, seal.ref)


def test_commit_review_resume_handles_pending_rollback(tmp_path: Path) -> None:
    """On resume, if rollback_pending is True, rollback is executed first and flag cleared."""
    _ = _init_repo(tmp_path)

    stray_file = tmp_path / "unwanted.tmp"
    stray_file.write_text("should be rolled back", encoding="utf-8")

    seal = seal_pending(tmp_path, run_id="run-103", iteration="0")
    preflight(tmp_path, seal.ref)

    # Simulated checkpoint before crash: rollback_pending=True
    crashed_state = PipelineState(
        phase="development",
        rollback_pending=True,
        rollback_ref=seal.ref,
        commit_review_rejections=1,
    )

    # Resume via _resolve_initial_state
    config = UnifiedConfig()
    bundle = _bundle()

    resumed = resolve_initial_state(
        config=config,
        policy_bundle=bundle,
        initial_state=crashed_state,
        counter_overrides=None,
        pipeline_deps=None,
        pro_hooks=None,
        state_factory=None,
        workspace_root=tmp_path,
    )

    # Rollback must have completed: unwanted.tmp removed
    assert not stray_file.exists()
    assert resumed.rollback_pending is False
    assert resumed.rollback_ref is None

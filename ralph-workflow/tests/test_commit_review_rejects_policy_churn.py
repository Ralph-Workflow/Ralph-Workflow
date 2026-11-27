from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from ralph.config.bootstrap import auto_seed_default_git_exclude, auto_seed_default_gitignore
from ralph.git.commit_review import untrack_engine_internal_files
from ralph.git.proposal_seal import seal_pending
from ralph.phases import PhaseContext
from ralph.phases.commit_review import (
    CommitReviewApproved,
    CommitReviewRejected,
    handle_commit_review_phase,
    is_agent_internal_path,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.workspace.fs import FsWorkspace

pytestmark = [pytest.mark.timeout_seconds(5), pytest.mark.subprocess_e2e]

_ARTIFACT_PATH = ".agent/artifacts/commit_review.md"


def _approve(repo_root: Path) -> None:
    untrack_engine_internal_files(repo_root, is_agent_internal_path)
    auto_seed_default_gitignore(repo_root)
    auto_seed_default_git_exclude(repo_root)
    tree_sha = seal_pending(repo_root, "run", 1).tree_sha
    path = repo_root / _ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        f"---\ntype: commit_review\nverdict: approve\ntree_sha: {tree_sha}\n---\n",
        encoding="utf-8",
    )


def _run_phase(repo_root: Path) -> list[object]:
    context = PhaseContext.construct(
        workspace=FsWorkspace(repo_root),
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        artifacts_policy=object(),
        agents_policy=object(),
    )
    effect = InvokeAgentEffect(
        agent_name="dev",
        phase="development_commit_review",
        prompt_file="cleanup.txt",
        run_id="phase-test-run",
        iteration="1",
    )
    return handle_commit_review_phase(effect, context)


def _tracked(repo_root: Path, rel_path: str, text: str) -> Path:
    path = repo_root / rel_path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    repo = Repo(repo_root)
    try:
        repo.index.add([rel_path])
        repo.index.commit(f"track {rel_path}")
    finally:
        repo.close()
    return path


_POLICY = "docs/ralph-workflow-policy/verification-policy.md"
_CHURNED_LINE = (
    "The census is current: 8 hold only BLOCKED rows of tmp/verify/profile_last_run.json.\n"
)


def test_an_approved_proposal_that_adds_volatile_evidence_to_the_policy_is_rejected(
    tmp_git_repo: Path,
) -> None:
    policy = _tracked(tmp_git_repo, _POLICY, "# Verification policy\n")
    policy.write_text("# Verification policy\n" + _CHURNED_LINE, encoding="utf-8")
    _approve(tmp_git_repo)

    events = _run_phase(tmp_git_repo)

    assert len(events) == 1
    event = events[0]
    assert isinstance(event, CommitReviewRejected)
    assert event.hard is True
    assert "tmp/verify/profile_last_run.json" in event.reason
    assert _POLICY in event.reason


def test_the_same_text_outside_the_policy_corpus_is_still_approved(tmp_git_repo: Path) -> None:
    notes = _tracked(tmp_git_repo, "docs/notes.md", "# Notes\n")
    notes.write_text("# Notes\n" + _CHURNED_LINE, encoding="utf-8")
    _approve(tmp_git_repo)

    events = _run_phase(tmp_git_repo)

    assert [type(event) for event in events] == [CommitReviewApproved]

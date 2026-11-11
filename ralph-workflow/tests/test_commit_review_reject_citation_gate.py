"""Black-box proof: a fabricated reject citation never authorises a rollback.

The commit-review gate may only roll the working tree back when the
reviewer's cited evidence verifies against the pending proposal diff. A
fabricated citation is a reviewer malfunction: the gate fails, the pending
work survives, and nothing is committed.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from ralph.config.bootstrap import auto_seed_default_git_exclude, auto_seed_default_gitignore
from ralph.git.commit_review import untrack_engine_internal_files
from ralph.git.proposal_seal import seal_pending
from ralph.phases import PhaseContext
from ralph.phases.commit_review import (
    CommitReviewGateFailed,
    CommitReviewRejected,
    handle_commit_review_phase,
    is_agent_internal_path,
)
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.workspace.fs import FsWorkspace

pytestmark = pytest.mark.timeout_seconds(5)

_ARTIFACT_PATH = ".agent/artifacts/commit_review.md"

#: Reject reasons whose claims cannot be true for a pending diff that holds
#: exactly ONE modified, non-binary ``src/app.py`` entry: a fabricated
#: binary count, a fabricated deleted path, a path outside the diff, and a
#: fabricated added-file count. A reviewer that cites any of them has
#: malfunctioned, and the engine must never treat the verdict as authority
#: for the destructive rollback path.
_FABRICATED_REASONS = (
    "src/app.py and 999 binary files were damaged",
    "src/app.py and README.md were deleted",
    "outside/src/app.py was deleted",
    "src/app.py and 1 added file were damaged",
    "src/app.py was modified and Makefile was deleted",
)


def _write_reject_artifact(workspace: FsWorkspace, reason: str) -> None:
    """Write a reject artifact whose tree SHA matches the current pending tree."""
    repo_root = Path(workspace.root)
    untrack_engine_internal_files(repo_root, is_agent_internal_path)
    auto_seed_default_gitignore(repo_root)
    auto_seed_default_git_exclude(repo_root)
    tree_sha = seal_pending(repo_root, "run", 1).tree_sha
    path = repo_root / _ARTIFACT_PATH
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        "\n".join(
            [
                "---",
                "type: commit_review",
                "verdict: reject",
                f"tree_sha: {tree_sha}",
                "---",
                "",
                "## Reason",
                f"- [R1] {reason}",
                "",
            ]
        ),
        encoding="utf-8",
    )


def _run_phase(workspace: FsWorkspace) -> list[object]:
    """Run the commit-review phase over the workspace and return its events."""
    context = PhaseContext.construct(
        workspace=workspace,
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
    )
    return handle_commit_review_phase(effect, context)


def _head_sha(repo_root: Path) -> str:
    """Return the SHA of the repository's current ``HEAD`` commit."""
    repo = Repo(repo_root)
    try:
        return str(repo.head.commit.hexsha)
    finally:
        repo.close()


def _commit_count(repo_root: Path) -> int:
    """Return the number of commits reachable from ``HEAD``."""
    repo = Repo(repo_root)
    try:
        return sum(1 for _ in repo.iter_commits())
    finally:
        repo.close()


def _track_and_commit(repo_root: Path, paths: list[str]) -> None:
    """Track ``paths`` and record them in a single commit."""
    repo = Repo(repo_root)
    try:
        repo.index.add(paths)
        repo.index.commit(f"track {', '.join(paths)}")
    finally:
        repo.close()


def test_fabricated_reject_citation_fails_the_gate_without_rolling_back(
    tmp_git_repo: Path,
) -> None:
    """A fabricated reject citation never authorises the rollback path.

    The reviewer malfunctioned: the engine cannot verify the citation, so
    the verdict is a gate failure. The pending work is NOT discarded --
    nothing is rolled back and nothing is committed.
    """
    app_file = tmp_git_repo / "src" / "app.py"
    app_file.parent.mkdir(parents=True, exist_ok=True)
    app_file.write_text("print('hello')\n", encoding="utf-8")
    tracked_makefile = tmp_git_repo / "Makefile"
    tracked_makefile.write_text("all:\n\t@true\n", encoding="utf-8")
    _track_and_commit(tmp_git_repo, ["src/app.py", "Makefile"])
    app_file.write_text("print('changed')\n", encoding="utf-8")

    workspace = FsWorkspace(tmp_git_repo)
    auto_seed_default_gitignore(tmp_git_repo)
    auto_seed_default_git_exclude(tmp_git_repo)
    head_before = _head_sha(tmp_git_repo)
    commits_before = _commit_count(tmp_git_repo)

    for fabricated_reason in _FABRICATED_REASONS:
        _write_reject_artifact(workspace, fabricated_reason)
        result = _run_phase(workspace)

        assert len(result) == 1
        event = result[0]
        assert isinstance(event, CommitReviewGateFailed)
        assert "citations could not be verified" in event.detail
        # A fabricated citation never discards work: the pending change survives.
        assert app_file.read_text(encoding="utf-8") == "print('changed')\n"
        assert _head_sha(tmp_git_repo) == head_before
        assert _commit_count(tmp_git_repo) == commits_before


def test_correctly_cited_reject_is_still_a_rejection(tmp_git_repo: Path) -> None:
    """The control: a verifiable citation is a rejection, not a malfunction.

    This is the positive counterpart of the proofs above: the ONLY
    difference is whether the cited path appears in the pending diff.
    """
    app_file = tmp_git_repo / "src" / "app.py"
    app_file.parent.mkdir(parents=True, exist_ok=True)
    app_file.write_text("print('hello')\n", encoding="utf-8")
    _track_and_commit(tmp_git_repo, ["src/app.py"])
    app_file.write_text("print('changed')\n", encoding="utf-8")

    workspace = FsWorkspace(tmp_git_repo)
    _write_reject_artifact(workspace, "src/app.py was corrupted by the change")

    result = _run_phase(workspace)

    assert len(result) == 1
    event = result[0]
    assert isinstance(event, CommitReviewRejected)
    assert event.hard is False
    assert "src/app.py" in event.reason

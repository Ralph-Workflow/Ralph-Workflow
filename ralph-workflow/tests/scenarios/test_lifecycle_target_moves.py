from __future__ import annotations

from pathlib import Path

import pytest

from tests._scenario_harness import AgentMode, FakeAgentRunner, commit_file, git, paused_text_rebase
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.required_auto_integrate_e2e, pytest.mark.timeout_seconds(12)]


def _move_target(root: Path, *, force: bool = False) -> str:
    old = git(root, "rev-parse", "main")
    parent = git(root, "rev-parse", f"{old}^") if force else old
    tree = git(root, "rev-parse", f"{parent}^{{tree}}")
    moved = git(root, "commit-tree", tree, "-p", parent, "-m", "target moved")
    git(root, "update-ref", "refs/heads/main", moved, old)
    return moved


@pytest.mark.parametrize("force", [False, True], ids=["target-moves", "target-force-pushed"])
def test_target_motion_is_reobserved_before_landing(tmp_git_repo: Path, force: bool) -> None:
    paused_text_rebase(tmp_git_repo)
    feature_sha = git(tmp_git_repo, "rev-parse", "feature")
    moved = _move_target(tmp_git_repo, force=force)

    run = drive_integration(tmp_git_repo, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))

    assert run.error is None, f"target motion must be reobserved: {run.error}"
    assert "main" in git(tmp_git_repo, "branch", "--contains", moved, "main", check=False), (
        "landing must be guarded against and recomputed after target motion"
    )
    assert_content_equivalence(tmp_git_repo, contract="the feature must land after target motion", feature=feature_sha, source_refs=(moved,))


def test_remote_rejection_keeps_local_landing_intact(tmp_git_repo: Path) -> None:
    commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature_sha = commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")
    git(tmp_git_repo, "remote", "add", "origin", str(tmp_git_repo / "missing-remote"))

    run = drive_integration(tmp_git_repo)

    assert run.error is None, f"an unreachable remote must not undo local landing: {run.error}"
    assert "main" in git(tmp_git_repo, "branch", "--contains", feature_sha, "main", check=False), (
        "publication failure must leave the verified local landing intact"
    )


@pytest.mark.parametrize("mode", ["sibling-first", "two-worktrees"])
def test_sibling_worktrees_share_one_landing_goal(tmp_git_repo: Path, mode: str) -> None:
    commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature_sha = commit_file(tmp_git_repo, "shared-change.txt", "same\n", "shared change")
    git(tmp_git_repo, "checkout", "-q", "main")
    sibling = tmp_git_repo.parent / f"sibling-{mode}"
    git(tmp_git_repo, "worktree", "add", "-q", "-b", f"sibling-{mode}", str(sibling), "main")
    commit_file(sibling, "shared-change.txt", "same\n", "same shared change")
    sibling_sha = git(sibling, "rev-parse", "HEAD")
    git(tmp_git_repo, "update-ref", "refs/heads/main", sibling_sha)
    git(tmp_git_repo, "checkout", "-q", "feature")

    run = drive_integration(tmp_git_repo)

    assert run.error is None, f"sibling landing must be observed as progress: {run.error}"
    assert_content_equivalence(tmp_git_repo, contract="concurrent worktrees must converge on one landed result", feature=feature_sha, source_refs=(sibling_sha,))

from __future__ import annotations

from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier

import pytest

from tests._scenario_harness import AgentMode, FakeAgentRunner, commit_file, git, paused_text_rebase
from tests.scenarios._content_equivalence import assert_content_equivalence, drive_integration

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(12),
]


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
    assert_content_equivalence(
        tmp_git_repo,
        contract="the feature must land after target motion",
        feature=feature_sha,
        source_refs=(moved,),
    )


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


def test_remote_force_push_is_reobserved_and_reconciled(tmp_git_repo: Path) -> None:
    base = commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    remote = tmp_git_repo.parent / "force-push-remote.git"
    git(tmp_git_repo.parent, "init", "-q", "--bare", str(remote))
    git(tmp_git_repo, "remote", "add", "origin", str(remote))
    git(tmp_git_repo, "checkout", "-q", "-b", "published-old")
    commit_file(tmp_git_repo, "old-remote.txt", "old\n", "old remote target")
    git(tmp_git_repo, "push", "-q", "origin", "published-old:refs/heads/main")
    git(tmp_git_repo, "checkout", "-q", "-b", "published-rewrite", base)
    remote_sha = commit_file(tmp_git_repo, "remote.txt", "remote\n", "rewritten remote target")
    git(tmp_git_repo, "push", "-q", "--force", "origin", "published-rewrite:refs/heads/main")
    git(tmp_git_repo, "checkout", "-q", "main")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature", base)
    feature_sha = commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")

    run = drive_integration(tmp_git_repo, config=_remote_enabled_config())

    assert run.error is None, f"force-pushed remote reconciliation must not crash: {run.error}"
    assert (tmp_git_repo / "remote.txt").read_text(encoding="utf-8") == "remote\n"
    assert (tmp_git_repo / "feature.txt").read_text(encoding="utf-8") == "feature\n"
    assert "main" in git(tmp_git_repo, "branch", "--contains", remote_sha, "main", check=False)
    assert_content_equivalence(
        tmp_git_repo,
        contract="force-pushed remote and local feature content must both survive reconciliation",
        feature=feature_sha,
        source_refs=(remote_sha,),
    )


def _remote_enabled_config():
    from ralph.config.models import UnifiedConfig

    return UnifiedConfig.model_validate(
        {
            "general": {
                "auto_integrate_enabled": True,
                "auto_integrate_target": "main",
                "auto_integrate_remote_enabled": True,
                "auto_integrate_remote": "origin",
            }
        }
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
    assert_content_equivalence(
        tmp_git_repo,
        contract="concurrent worktrees must converge on one landed result",
        feature=feature_sha,
        source_refs=(sibling_sha,),
    )


def test_two_worktrees_race_through_one_guarded_landing(tmp_git_repo: Path) -> None:
    base = commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    first = tmp_git_repo.parent / "concurrent-first"
    second = tmp_git_repo.parent / "concurrent-second"
    git(tmp_git_repo, "worktree", "add", "-q", "-b", "feature-first", str(first), base)
    git(tmp_git_repo, "worktree", "add", "-q", "-b", "feature-second", str(second), base)
    commit_file(first, "first.txt", "first\n", "first side")
    commit_file(first, "second.txt", "second\n", "both sides from first")
    commit_file(second, "first.txt", "first\n", "both sides from second")
    second_sha = commit_file(second, "second.txt", "second\n", "second side")
    first_sha = git(first, "rev-parse", "HEAD")
    git(tmp_git_repo, "checkout", "-q", "--detach", base)
    start = Barrier(2)

    def integrate(root: Path):
        start.wait(timeout=5)
        return drive_integration(root)

    reflog_before = git(tmp_git_repo, "reflog", "show", "--format=%H", "main").splitlines()
    with ThreadPoolExecutor(max_workers=2) as executor:
        futures = (executor.submit(integrate, first), executor.submit(integrate, second))
        concurrent_runs = tuple(future.result(timeout=10) for future in futures)
    reflog_after_race = git(tmp_git_repo, "reflog", "show", "--format=%H", "main").splitlines()

    assert all(run.error is None for run in concurrent_runs)
    assert len(reflog_after_race) - len(reflog_before) == 1, (
        "the synchronized race must have one guarded winner and must not move the target twice"
    )

    followups = (drive_integration(first), drive_integration(second))
    assert all(run.error is None for run in followups)
    target = git(tmp_git_repo, "rev-parse", "main")
    assert git(tmp_git_repo, "show", f"{target}:first.txt") == "first"
    assert git(tmp_git_repo, "show", f"{target}:second.txt") == "second"
    assert_content_equivalence(
        tmp_git_repo,
        contract="both concurrent worktree changes must survive guarded landing",
        target=target,
        feature=second_sha,
        source_refs=(first_sha,),
    )

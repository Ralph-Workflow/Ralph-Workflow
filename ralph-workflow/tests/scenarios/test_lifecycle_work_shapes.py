"""Catalog group 2: the *shape* of the work that has to reach the target.

Few commits versus very many, one hot conflict repeated on every commit
versus unrelated conflicts, a range that contains merge commits and a range
that contains already-published commits.  The size of the work must never
decide the outcome: every shape ends in landed-and-verified.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._scenario_harness import AgentMode, FakeAgentRunner, commit_file, git
from tests.scenarios._content_equivalence import (
    FastImportCommit,
    assert_content_equivalence,
    drive_integration,
    fast_import_commits,
)

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(15),
]

LANDED = "the feature's content must be present on the target branch"


def _seed_base(root: Path) -> str:
    return commit_file(root, "base.txt", "base\n", "scenario base")


def _few_commits(root: Path) -> str:
    _seed_base(root)
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "one.txt", "one\n", "feature one")
    commit_file(root, "two.txt", "two\n", "feature two")
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def _many_commits(root: Path, count: int) -> str:
    base = _seed_base(root)
    commits = [
        FastImportCommit(
            ref="refs/heads/feature",
            message=f"feature commit {index}",
            files=((f"file-{index}.txt", f"content {index}\n".encode()),),
            parent_sha=base if index == 0 else None,
            parent_index=index - 1 if index else None,
        )
        for index in range(count)
    ]
    fast_import_commits(root, commits)
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def _hot_conflict(root: Path) -> str:
    """Every replayed commit touches the same file the target changed."""
    _seed_base(root)
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "hot.txt", "feature one\n", "feature one")
    commit_file(root, "hot.txt", "feature two\n", "feature two")
    commit_file(root, "hot.txt", "feature three\n", "feature three")
    git(root, "checkout", "-q", "main")
    commit_file(root, "hot.txt", "target side\n", "target change")
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def _unrelated_conflicts(root: Path) -> str:
    """Each replayed commit conflicts on a different file."""
    _seed_base(root)
    git(root, "checkout", "-q", "-b", "feature")
    for index in range(4):
        commit_file(root, f"file-{index}.txt", f"feature {index}\n", f"feature {index}")
    git(root, "checkout", "-q", "main")
    for index in range(4):
        commit_file(root, f"file-{index}.txt", f"target {index}\n", f"target {index}")
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def _range_with_merge_commit(root: Path) -> str:
    _seed_base(root)
    git(root, "checkout", "-q", "-b", "side")
    commit_file(root, "side.txt", "side\n", "side work")
    git(root, "checkout", "-q", "feature")
    commit_file(root, "feature.txt", "feature\n", "feature work")
    git(root, "merge", "-q", "--no-ff", "-m", "merge side", "side")
    git(root, "checkout", "-q", "main")
    return git(root, "rev-parse", "HEAD")


def _range_with_published_commit(root: Path) -> tuple[str, str]:
    """The feature range contains a commit that is already published."""
    _seed_base(root)
    published = commit_file(root, "published.txt", "published\n", "published commit")
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "work.txt", "work\n", "feature work")
    git(root, "checkout", "-q", "feature")
    return published, git(root, "rev-parse", "HEAD")


def test_few_commits_land(tmp_git_repo: Path) -> None:
    feature_sha = _few_commits(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = drive_integration(tmp_git_repo)

    assert run.error is None, f"the tick must not crash on a two-commit feature: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_many_commits_land(tmp_git_repo: Path) -> None:
    feature_sha = _many_commits(tmp_git_repo, 12)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = drive_integration(tmp_git_repo)

    assert run.error is None, f"the tick must not crash on a many-commit feature: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_one_hot_conflict_repeated_on_every_commit_still_lands(tmp_git_repo: Path) -> None:
    feature_sha = _hot_conflict(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = drive_integration(tmp_git_repo, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))

    assert run.error is None, f"the tick must not crash on a repeated hot conflict: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_unrelated_conflicts_across_many_files_land(tmp_git_repo: Path) -> None:
    feature_sha = _unrelated_conflicts(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = drive_integration(tmp_git_repo, resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES))

    assert run.error is None, f"the tick must not crash on unrelated conflicts: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_range_containing_merge_commits_still_lands(tmp_git_repo: Path) -> None:
    feature_sha = _range_with_merge_commit(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = drive_integration(tmp_git_repo)

    assert run.error is None, f"the tick must not crash on a merge commit in the range: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_range_containing_published_commits_lands_without_rewriting_them(
    tmp_git_repo: Path,
) -> None:
    published_sha, feature_sha = _range_with_published_commit(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = drive_integration(tmp_git_repo)

    assert run.error is None, f"the tick must not crash on a published commit in the range: {run.error}"
    assert "main" in git(tmp_git_repo, "branch", "--contains", published_sha, "main", check=False), (
        "an already-published commit must stay reachable from the target, never rewritten"
    )
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )

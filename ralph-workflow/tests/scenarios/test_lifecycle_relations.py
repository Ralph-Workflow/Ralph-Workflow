"""Catalog group 1: the relation between the feature branch and the target.

Every relation named by the scenario catalog must end in *landed and verified*:
the feature's content is reachable from the target tip and nothing else
changed.  The relation is observed from git, never assumed.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from tests._scenario_harness import AgentMode, FakeAgentRunner, commit_file, git
from tests.scenarios._content_equivalence import (
    assert_content_equivalence,
    drive_integration,
)

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]

LANDED = "the feature's content must be present on the target branch"


def _seed_base(root: Path) -> None:
    commit_file(root, "base.txt", "base\n", "scenario base")


def _feature_ahead(root: Path) -> str:
    _seed_base(root)
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "feature.txt", "feature\n", "feature change")
    return git(root, "rev-parse", "HEAD")


def _target_ahead(root: Path) -> str:
    _seed_base(root)
    git(root, "checkout", "-q", "-b", "feature")
    commit_file(root, "feature.txt", "feature\n", "feature change")
    git(root, "checkout", "-q", "main")
    commit_file(root, "target.txt", "target\n", "target change")
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def _diverged(root: Path, *, conflicting: bool) -> str:
    _seed_base(root)
    git(root, "checkout", "-q", "-b", "feature")
    if conflicting:
        commit_file(root, "shared.txt", "feature side\n", "feature change")
    else:
        commit_file(root, "feature.txt", "feature\n", "feature change")
    git(root, "checkout", "-q", "main")
    if conflicting:
        commit_file(root, "shared.txt", "target side\n", "target change")
    else:
        commit_file(root, "target.txt", "target\n", "target change")
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def _unrelated_histories(root: Path) -> str:
    _seed_base(root)
    git(root, "checkout", "-q", "--orphan", "feature")
    git(root, "rm", "-q", "-rf", ".")
    commit_file(root, "other.txt", "other\n", "unrelated root commit")
    git(root, "checkout", "-q", "main")
    git(root, "checkout", "-q", "feature")
    return git(root, "rev-parse", "HEAD")


def test_feature_already_contained_in_the_target_changes_nothing(tmp_git_repo: Path) -> None:
    _seed_base(tmp_git_repo)
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature change")
    git(tmp_git_repo, "checkout", "-q", "main")
    git(tmp_git_repo, "merge", "-q", "--no-ff", "-m", "landed by hand", "feature")
    git(tmp_git_repo, "checkout", "-q", "feature")
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = drive_integration(tmp_git_repo)

    assert run.error is None, f"the tick must not crash on an already-contained feature: {run.error}"
    target_after = git(tmp_git_repo, "rev-parse", "main")
    assert target_after == target_before, (
        "a feature already contained in the target must leave the target untouched "
        f"(target moved from {target_before} to {target_after})"
    )


def test_feature_strictly_ahead_fast_forwards_the_target(tmp_git_repo: Path) -> None:
    feature_sha = _feature_ahead(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = drive_integration(tmp_git_repo)

    assert run.error is None, f"the tick must not crash on a strictly-ahead feature: {run.error}"
    assert git(tmp_git_repo, "rev-parse", "main") != target_before, (
        "a strictly-ahead feature must move the target forward"
    )
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_target_strictly_ahead_carries_the_feature_work_forward(tmp_git_repo: Path) -> None:
    feature_sha = _target_ahead(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = drive_integration(tmp_git_repo)

    assert run.error is None, f"the tick must not crash on a strictly-ahead target: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )
    assert (tmp_git_repo / "target.txt").is_file(), (
        "the target's own work must survive the integration of the feature"
    )


def test_diverged_without_conflicts_lands_the_feature(tmp_git_repo: Path) -> None:
    feature_sha = _diverged(tmp_git_repo, conflicting=False)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = drive_integration(tmp_git_repo)

    assert run.error is None, f"the tick must not crash on a conflict-free divergence: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_diverged_with_conflicts_lands_the_resolved_feature(tmp_git_repo: Path) -> None:
    feature_sha = _diverged(tmp_git_repo, conflicting=True)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = drive_integration(
        tmp_git_repo,
        resolver=FakeAgentRunner(AgentMode.EDITS_AND_STAGES),
    )

    assert run.error is None, f"the tick must not crash on a conflicted divergence: {run.error}"
    assert run.resolver_launches >= 1, "a conflicted divergence must reach the resolver"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )


def test_unrelated_histories_land_the_feature_content(tmp_git_repo: Path) -> None:
    feature_sha = _unrelated_histories(tmp_git_repo)
    target_before = git(tmp_git_repo, "rev-parse", "main")

    run = drive_integration(tmp_git_repo)

    assert run.error is None, f"the tick must not crash on unrelated histories: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract=LANDED,
        feature=feature_sha,
        source_refs=(target_before,),
    )

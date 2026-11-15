from __future__ import annotations

from pathlib import Path

import pytest

from tests._scenario_harness import commit_file, git
from tests.scenarios._content_equivalence import (
    assert_content_equivalence,
    content_equivalence,
    drive_integration,
    snapshot_uncommitted,
)

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.required_auto_integrate_e2e,
    pytest.mark.timeout_seconds(10),
]


def test_final_landing_check_proves_content_and_uncommitted_work(tmp_git_repo: Path) -> None:
    commit_file(tmp_git_repo, "base.txt", "base\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature_sha = commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")
    (tmp_git_repo / "untracked.txt").write_text("preserve me\n", encoding="utf-8")
    preserved = snapshot_uncommitted(tmp_git_repo, ("untracked.txt",))
    target_before = git(tmp_git_repo, "rev-parse", "main")
    run = drive_integration(tmp_git_repo)
    assert run.error is None, f"the landing used by the final check must not crash: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="every non-excluded feature change must be on target, with no unrelated or uncommitted loss",
        feature=feature_sha,
        source_refs=(target_before,),
        preserved=preserved,
    )


def test_content_equivalence_helper_rejects_a_lost_feature_change(tmp_git_repo: Path) -> None:
    base = commit_file(tmp_git_repo, "shared.txt", "base\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature = commit_file(tmp_git_repo, "shared.txt", "feature\n", "feature")
    git(tmp_git_repo, "checkout", "-q", "main")
    report = content_equivalence(
        tmp_git_repo, target="main", feature=feature, base=base, source_refs=("main",)
    )
    assert not report.satisfied and report.missing, (
        "the reusable content-equivalence oracle must reject a target that lost feature content"
    )

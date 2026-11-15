from __future__ import annotations

from pathlib import Path

import pytest

from tests._scenario_harness import commit_file, git
from tests.scenarios._content_equivalence import (
    assert_content_equivalence,
    capture_content_snapshot,
    content_equivalence,
    drive_integration,
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
    snapshot = capture_content_snapshot(
        tmp_git_repo,
        feature=feature_sha,
        preserved_paths=("untracked.txt",),
    )
    run = drive_integration(tmp_git_repo)
    assert run.error is None, f"the landing used by the final check must not crash: {run.error}"
    assert_content_equivalence(
        tmp_git_repo,
        contract="every non-excluded feature change must be on target, with no unrelated or uncommitted loss",
        snapshot=snapshot,
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


def test_frozen_proof_rejects_missing_feature_after_ancestry_changes(tmp_git_repo: Path) -> None:
    base = commit_file(tmp_git_repo, "shared.txt", "base\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature = commit_file(tmp_git_repo, "shared.txt", "feature\n", "feature")
    snapshot = capture_content_snapshot(tmp_git_repo, feature=feature)
    git(tmp_git_repo, "checkout", "-q", "main")
    git(tmp_git_repo, "merge", "-q", "--ff-only", feature)
    commit_file(tmp_git_repo, "shared.txt", "base\n", "lose feature after landing")

    report = content_equivalence(tmp_git_repo, snapshot=snapshot)

    assert base == snapshot.base
    assert feature == snapshot.feature
    assert not report.satisfied and report.missing == (
        "shared.txt (the target still carries the pre-feature content)",
    )


def test_frozen_proof_rejects_unrelated_change_to_existing_path(tmp_git_repo: Path) -> None:
    commit_file(tmp_git_repo, "stable.txt", "stable\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature = commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")
    snapshot = capture_content_snapshot(tmp_git_repo, feature=feature)
    git(tmp_git_repo, "checkout", "-q", "main")
    commit_file(tmp_git_repo, "stable.txt", "unrelated\n", "unrelated modification")
    git(tmp_git_repo, "merge", "-q", "--no-ff", "-m", "land feature", feature)

    report = content_equivalence(tmp_git_repo, snapshot=snapshot)

    assert not report.satisfied and report.unexpected == (
        "stable.txt (content differs from the pre-integration source tree)",
    )


def test_frozen_proof_rejects_extra_unrelated_path(tmp_git_repo: Path) -> None:
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature = commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")
    snapshot = capture_content_snapshot(tmp_git_repo, feature=feature)
    git(tmp_git_repo, "checkout", "-q", "main")
    git(tmp_git_repo, "merge", "-q", "--ff-only", feature)
    commit_file(tmp_git_repo, "stray.txt", "stray\n", "unrelated path")

    report = content_equivalence(tmp_git_repo, snapshot=snapshot)

    assert not report.satisfied and report.unexpected == ("stray.txt",)


@pytest.mark.parametrize("kind", ["tracked", "untracked"])
def test_frozen_proof_rejects_lost_uncommitted_bytes(tmp_git_repo: Path, kind: str) -> None:
    commit_file(tmp_git_repo, "tracked.txt", "committed\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature = commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")
    relative = "tracked.txt" if kind == "tracked" else "untracked.txt"
    (tmp_git_repo / relative).write_bytes(b"dirty\x00bytes\n")
    snapshot = capture_content_snapshot(
        tmp_git_repo,
        feature=feature,
        preserved_paths=(relative,),
    )
    (tmp_git_repo / relative).write_bytes(b"lost\n")

    report = content_equivalence(tmp_git_repo, snapshot=snapshot)

    assert not report.satisfied and report.lost == (relative,)


def test_frozen_proof_accepts_legitimate_feature_change(tmp_git_repo: Path) -> None:
    commit_file(tmp_git_repo, "stable.txt", "stable\n", "base")
    git(tmp_git_repo, "checkout", "-q", "-b", "feature")
    feature = commit_file(tmp_git_repo, "feature.txt", "feature\n", "feature")
    snapshot = capture_content_snapshot(tmp_git_repo, feature=feature)
    git(tmp_git_repo, "checkout", "-q", "main")
    git(tmp_git_repo, "merge", "-q", "--ff-only", feature)

    report = content_equivalence(tmp_git_repo, snapshot=snapshot)

    assert report.satisfied, report.describe()

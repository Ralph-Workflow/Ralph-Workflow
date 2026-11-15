from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo
from git.objects.tree import Tree

from ralph.config.bootstrap import auto_seed_default_git_exclude, auto_seed_default_gitignore
from ralph.git.proposal_seal import DiffEntry, diff_tree, preflight, rollback, seal_pending
from ralph.git.subprocess_runner import run_git
from ralph.phases.commit_review import (
    CommitReviewGateFailed,
    CommitReviewRejected,
    TripwireThresholds,
    evaluate_tripwires,
    run_commit_review_stage1_preflight,
)
from ralph.pipeline.reducer import reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

_DEFAULTS_DIR = Path(__file__).resolve().parents[1] / "ralph" / "policy" / "defaults"

pytestmark = [
    pytest.mark.subprocess_e2e,
    pytest.mark.timeout_seconds(20),
    pytest.mark.required_auto_integrate_e2e,
]

_SRC_FILES = ("src/app.py", "src/util.py")


def _git(repo_root: Path, *args: str) -> None:
    res = run_git(args, cwd=repo_root, label="test-commit-review-real-diff")
    assert res.returncode == 0, f"git command failed: {res.stderr}"


def _git_rm(repo_root: Path, *paths: str) -> None:
    repo = Repo(repo_root)
    try:
        repo.index.remove(list(paths), working_tree=True)
    finally:
        repo.close()


def _init_repo(repo_root: Path, files: dict[str, str]) -> None:
    repo = Repo(repo_root)
    try:
        readme = repo_root / "README.md"
        if readme.exists():
            repo.index.remove(["README.md"], working_tree=True)
        for relative, content in files.items():
            target = repo_root / relative
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(content, encoding="utf-8")
        repo.index.add(list(files.keys()))
        repo.index.commit("initial")
    finally:
        repo.close()


def _seed_engine_defaults(repo_root: Path) -> None:
    auto_seed_default_gitignore(repo_root)
    auto_seed_default_git_exclude(repo_root)


def _module_paths(count: int) -> list[str]:
    return [f"module_{index:02d}.py" for index in range(count)]


def _count_tracked_paths(tree: Tree) -> int:
    total = 0
    for entry in tree:
        if isinstance(entry, Tree):
            total += _count_tracked_paths(entry)
        else:
            total += 1
    return total


def _tracked_count(repo_root: Path) -> int:
    repo = Repo(repo_root)
    try:
        tree = repo.head.commit.tree
        return _count_tracked_paths(tree)
    finally:
        repo.close()


def test_a_protected_top_level_directory_deleted_in_its_entirety_is_a_hard_tripwire(
    tmp_git_repo: Path,
) -> None:
    repo_root = tmp_git_repo
    _init_repo(repo_root, dict.fromkeys(_SRC_FILES, "content\n"))

    for path in _SRC_FILES:
        (repo_root / path).unlink()
    seal = seal_pending(repo_root, "run-entire", 1)

    report = evaluate_tripwires(
        diff_tree(repo_root, seal.ref),
        _tracked_count(repo_root),
        TripwireThresholds(),
    )

    findings = [f for f in report.hard if f.category == "protected_deletion"]
    assert len(findings) == 1, report.hard
    assert findings[0].path == "src"
    assert "in its entirety" in findings[0].message


def test_deleting_only_some_files_of_a_protected_directory_is_not_an_entirety_deletion(
    tmp_git_repo: Path,
) -> None:
    repo_root = tmp_git_repo
    _init_repo(repo_root, dict.fromkeys(_SRC_FILES, "content\n"))

    (repo_root / _SRC_FILES[0]).unlink()
    seal = seal_pending(repo_root, "run-partial", 1)

    report = evaluate_tripwires(
        diff_tree(repo_root, seal.ref),
        _tracked_count(repo_root),
        TripwireThresholds(),
    )

    assert not [f for f in report.hard if f.category == "protected_deletion"]


def test_a_renamed_file_is_removed_by_the_rollback_so_the_tree_equals_the_target(
    tmp_git_repo: Path,
) -> None:
    repo_root = tmp_git_repo
    _init_repo(repo_root, {"keep.txt": "keep\n", "old_name.txt": "payload\n"})

    _git(repo_root, "mv", "old_name.txt", "new_name.txt")
    seal = seal_pending(repo_root, "run-rename", 1)

    rollback(repo_root, seal.ref)

    assert not (repo_root / "new_name.txt").exists()
    assert (repo_root / "old_name.txt").read_text(encoding="utf-8") == "payload\n"


def test_a_staged_secret_is_detected_by_content_in_a_real_diff(tmp_git_repo: Path) -> None:
    repo_root = tmp_git_repo
    _init_repo(repo_root, {"config.txt": "placeholder\n"})

    (repo_root / "config.txt").write_text(
        "AWS_ACCESS_KEY_ID=AKIAIOSFODNN7EXAMPLE\n",
        encoding="utf-8",
    )
    seal = seal_pending(repo_root, "run-secret", 1)

    report = evaluate_tripwires(
        diff_tree(repo_root, seal.ref),
        _tracked_count(repo_root),
        TripwireThresholds(),
    )

    assert any(f.category == "secret" for f in report.soft), report.soft


def test_a_path_outside_the_configured_allowed_directories_is_reported(
    tmp_git_repo: Path,
) -> None:
    repo_root = tmp_git_repo
    _init_repo(repo_root, {"src/app.py": "content\n", "docs/guide.md": "content\n"})

    (repo_root / "docs/guide.md").write_text("changed\n", encoding="utf-8")
    seal = seal_pending(repo_root, "run-scope", 1)

    report = evaluate_tripwires(
        diff_tree(repo_root, seal.ref),
        _tracked_count(repo_root),
        TripwireThresholds(),
        allowed_dirs=["src"],
    )

    assert any(f.category == "out_of_scope" and f.path == "docs/guide.md" for f in report.soft)


def test_staged_deletions_trip_the_mass_deletion_fraction_against_the_immutable_base(
    tmp_git_repo: Path,
) -> None:
    repo_root = tmp_git_repo
    _init_repo(repo_root, dict.fromkeys(_module_paths(10), "content\n"))
    _seed_engine_defaults(repo_root)

    deleted = _module_paths(5)
    _git_rm(repo_root, *deleted)
    repo = Repo(repo_root)
    try:
        assert not set(deleted) & set(repo.git.ls_files().splitlines())
    finally:
        repo.close()

    outcome = run_commit_review_stage1_preflight(
        repo_root,
        "run-staged-deletion",
        "1",
        None,
        "development_commit_review",
    )

    assert isinstance(outcome, CommitReviewRejected), outcome
    assert outcome.hard is True
    assert "Mass deletion" in outcome.reason
    assert "(5/11)" in outcome.reason


def test_staged_additions_cannot_dilute_the_mass_deletion_fraction(
    tmp_git_repo: Path,
) -> None:
    repo_root = tmp_git_repo
    _init_repo(repo_root, dict.fromkeys(_module_paths(10), "content\n"))
    _seed_engine_defaults(repo_root)

    _git_rm(repo_root, *_module_paths(4))
    new_files = [f"added_{index:02d}.py" for index in range(12)]
    for name in new_files:
        (repo_root / name).write_text("new\n", encoding="utf-8")
    repo = Repo(repo_root)
    try:
        repo.index.add(new_files)
    finally:
        repo.close()

    outcome = run_commit_review_stage1_preflight(
        repo_root,
        "run-staged-additions",
        "1",
        None,
        "development_commit_review",
    )

    assert isinstance(outcome, CommitReviewRejected), outcome
    assert outcome.hard is True
    assert "Mass deletion" in outcome.reason
    assert "(4/11)" in outcome.reason


def test_a_small_staged_deletion_does_not_trip_the_mass_deletion_fraction(
    tmp_git_repo: Path,
) -> None:
    repo_root = tmp_git_repo
    _init_repo(repo_root, dict.fromkeys(_module_paths(20), "content\n"))
    _seed_engine_defaults(repo_root)

    _git_rm(repo_root, *_module_paths(2))

    outcome = run_commit_review_stage1_preflight(
        repo_root,
        "run-small-deletion",
        "1",
        None,
        "development_commit_review",
    )

    assert not isinstance(outcome, (CommitReviewRejected, CommitReviewGateFailed)), outcome
    assert isinstance(outcome, tuple)
    _seal, _diff_entries, report, _base = outcome
    assert not [f for f in report.hard if f.category == "mass_deletion"]


def test_an_unavailable_base_membership_fails_the_gate_without_discarding_work(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def _without_membership(*_args: object, **_kwargs: object) -> list[DiffEntry]:
        return [
            DiffEntry(
                path="module_00.py",
                status="D",
                old_mode="100644",
                new_mode="000000",
                size=0,
                is_binary=False,
                content=None,
                base_tree_paths=None,
            )
        ]

    repo_root = tmp_git_repo
    _init_repo(repo_root, dict.fromkeys(_module_paths(4), "content\n"))
    _seed_engine_defaults(repo_root)
    (repo_root / "module_00.py").unlink()

    monkeypatch.setattr("ralph.phases.commit_review.diff_tree", _without_membership)

    outcome = run_commit_review_stage1_preflight(
        repo_root,
        "run-missing-membership",
        "1",
        None,
        "development_commit_review",
    )

    assert isinstance(outcome, CommitReviewGateFailed), outcome
    assert not isinstance(outcome, CommitReviewRejected), (
        "An unmeasurable threshold must never reject: a rejection rolls the tree back"
    )
    assert "membership" in outcome.detail
    assert "NOT rejected" in outcome.detail
    assert not (repo_root / "module_00.py").exists()
    assert (repo_root / "module_01.py").exists()
    preflight(repo_root, "refs/ralph/proposals/run-missing-membership/1")


def test_an_unavailable_base_membership_charges_no_rejection_and_asks_for_no_rollback(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:

    def _without_membership(*_args: object, **_kwargs: object) -> list[DiffEntry]:
        return [
            DiffEntry(
                path="module_00.py",
                status="D",
                old_mode="100644",
                new_mode="000000",
                size=0,
                is_binary=False,
                content=None,
                base_tree_paths=None,
            )
        ]

    repo_root = tmp_git_repo
    _init_repo(repo_root, dict.fromkeys(_module_paths(4), "content\n"))
    _seed_engine_defaults(repo_root)
    (repo_root / "module_00.py").unlink()

    monkeypatch.setattr("ralph.phases.commit_review.diff_tree", _without_membership)

    outcome = run_commit_review_stage1_preflight(
        repo_root,
        "run-missing-membership",
        "1",
        None,
        "development_commit_review",
    )
    assert isinstance(outcome, CommitReviewGateFailed), outcome

    policy = load_policy(_DEFAULTS_DIR).pipeline
    next_state, _ = reduce(PipelineState(phase="development_commit_review"), outcome, policy)

    assert next_state.phase == "failed_terminal"
    assert next_state.commit_review_rejections == 0, (
        "A gate failure must not charge the rejection counter"
    )
    assert next_state.rollback_pending is False, "A gate failure must not ask for a rollback"
    assert next_state.rollback_ref is None
    assert not (repo_root / "module_00.py").exists()
    assert (repo_root / "module_01.py").exists()

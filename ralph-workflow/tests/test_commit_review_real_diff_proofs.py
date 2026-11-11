"""Black-box proofs for commit-review behaviour that needs a REAL git diff.

The unit-level tripwire tests drive synthetic diff entries. These proofs
drive the real pipeline seams - a real temporary git repository, the real
proposal seal, the real ``git diff-tree -r`` diff and the real rollback -
so a behaviour that a recursive diff never produces cannot pass vacuously.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from ralph.config.bootstrap import auto_seed_default_git_exclude, auto_seed_default_gitignore
from ralph.git.proposal_seal import DiffEntry, diff_tree, rollback, seal_pending
from ralph.phases.commit_review import (
    CommitReviewGateFailed,
    CommitReviewRejected,
    TripwireThresholds,
    evaluate_tripwires,
    run_commit_review_stage1_preflight,
)

pytestmark = pytest.mark.timeout_seconds(20)

#: A protected top-level source directory and the files it holds.
_SRC_FILES = ("src/app.py", "src/util.py")


def _git_output(repo_root: Path, *args: str) -> str:
    """Run a git command in ``repo_root`` and return its standard output."""
    if not (repo_root / ".git").exists():
        repo = Repo.init(repo_root, initial_branch="main")
    else:
        repo = Repo(repo_root)
    try:
        git_cmd = getattr(repo.git, args[0].replace("-", "_"))
        return str(git_cmd(*args[1:]))
    finally:
        repo.close()


def _git(repo_root: Path, *args: str) -> None:
    """Run a git command in ``repo_root`` and fail the test when it errors."""
    _git_output(repo_root, *args)


def _git_rm(repo_root: Path, *paths: str) -> None:
    """Remove paths from the index and working tree without spawning a git process."""
    repo = Repo(repo_root)
    try:
        repo.index.remove(list(paths), working_tree=True)
    finally:
        repo.close()


def _init_repo(repo_root: Path, files: dict[str, str]) -> None:
    """Create a repository whose HEAD holds exactly ``files``."""
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
    """Seed the engine's canonical ignore/exclude files before a proof starts.

    The commit-review phase seeds these files itself, and the ``.gitignore``
    seed is a DETERMINISTIC COMMIT. Seeding them up front makes the phase's
    own seeding a no-op, so a proof measures the gate and not the seeding.
    """
    auto_seed_default_gitignore(repo_root)
    auto_seed_default_git_exclude(repo_root)


def _module_paths(count: int) -> list[str]:
    """Return the repository-relative paths of ``count`` tracked modules."""
    return [f"module_{index:02d}.py" for index in range(count)]


def _tracked_count(repo_root: Path) -> int:
    """Return the number of tracked paths in HEAD."""
    repo = Repo(repo_root)
    try:
        return sum(1 for item in repo.head.commit.tree.traverse() if item.type == "blob")
    finally:
        repo.close()


def test_a_protected_top_level_directory_deleted_in_its_entirety_is_a_hard_tripwire(
    tmp_git_repo: Path,
) -> None:
    """Deleting EVERY tracked file under a protected directory trips the gate.

    A recursive ``git diff-tree`` reports blob entries only, so the removal of
    a whole directory arrives as one deleted blob path per tracked file. The
    real diff - not a synthetic entry - is what proves the finding fires.
    """
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
    """A PARTIAL deletion is not an entirety deletion and stays silent."""
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
    """A rename leaves the NEW path behind unless the rollback removes it.

    The rejected state is sealed, then the tree is restored. The renamed
    file's new path is in the proposal tree and absent from the target, so
    the rollback must remove it - otherwise the tree no longer equals the
    target and the run would judge a tree the review never approved.
    """
    repo_root = tmp_git_repo
    _init_repo(repo_root, {"keep.txt": "keep\n", "old_name.txt": "payload\n"})

    _git(repo_root, "mv", "old_name.txt", "new_name.txt")
    seal = seal_pending(repo_root, "run-rename", 1)

    rollback(repo_root, seal.ref)

    assert not (repo_root / "new_name.txt").exists()
    assert (repo_root / "old_name.txt").read_text(encoding="utf-8") == "payload\n"


def test_a_staged_secret_is_detected_by_content_in_a_real_diff(tmp_git_repo: Path) -> None:
    """A credential in the diff CONTENT is reported, not only its file name.

    The content scan only works when the diff carries the blob text, so
    this proof drives the real diff of a real repository.
    """
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
    """A configured ``allowed_directories`` scopes the review."""
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
    """A staged deletion that exceeds the fraction is a hard tripwire.

    The real index no longer lists the deleted paths, so a denominator read
    from that index would shrink with the deletion. The denominator is the
    IMMUTABLE base tree the proposal was diffed against: 5 of its 11 tracked
    paths, not 5 of the 6 the real index still lists.
    """
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
    """Staged additions do not dilute the mass-deletion fraction.

    The additions are tracked by the real index, so an index-sized
    denominator would report 4 of 19 tracked files and stay silent. The
    immutable base tree holds 11 tracked paths, so 4 of them is 36.4%.
    """
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
    """A couple of staged deletions in a large repository is not a mass deletion."""
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


def test_an_unavailable_base_membership_fails_the_gate_closed(
    tmp_git_repo: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """A diff without the immutable membership is refused, not silently accepted.

    The membership is the denominator of the mass-deletion fraction. When the
    git layer cannot read it, the fraction cannot be evaluated, so the
    proposal is refused instead of letting the check lapse.
    """

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

    assert isinstance(outcome, CommitReviewRejected), outcome
    assert outcome.hard is True
    assert "Mass deletion" in outcome.reason
    assert "immutable base-tree membership is unavailable" in outcome.reason

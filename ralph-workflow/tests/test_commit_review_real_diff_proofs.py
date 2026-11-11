"""Black-box proofs for commit-review behaviour that needs a REAL git diff.

The unit-level tripwire tests drive synthetic diff entries. These proofs
drive the real pipeline seams - a real temporary git repository, the real
proposal seal, the real ``git diff-tree -r`` diff and the real rollback -
so a behaviour that a recursive diff never produces cannot pass vacuously.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest

from ralph.git.proposal_seal import diff_tree, rollback, seal_pending
from ralph.phases.commit_review import (
    TripwireThresholds,
    evaluate_tripwires,
)

pytestmark = pytest.mark.timeout_seconds(20)

#: A protected top-level source directory and the files it holds.
_SRC_FILES = ("src/app.py", "src/util.py")


def _git(repo_root: Path, *args: str) -> None:
    """Run a git command in ``repo_root`` and fail the test when it errors."""
    subprocess.run(
        ["git", *args],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )


def _init_repo(repo_root: Path, files: dict[str, str]) -> None:
    """Create a repository whose HEAD holds exactly ``files``."""
    repo_root.mkdir(parents=True, exist_ok=True)
    _git(repo_root, "init", "-q", "-b", "main")
    _git(repo_root, "config", "user.email", "dev@example.com")
    _git(repo_root, "config", "user.name", "dev")
    for relative, content in files.items():
        target = repo_root / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")
    _git(repo_root, "add", "-A")
    _git(repo_root, "commit", "-q", "-m", "initial")


def _tracked_count(repo_root: Path) -> int:
    """Return the number of tracked paths in HEAD."""
    result = subprocess.run(
        ["git", "ls-tree", "-r", "--name-only", "HEAD"],
        cwd=str(repo_root),
        check=True,
        capture_output=True,
        text=True,
    )
    return len([line for line in result.stdout.splitlines() if line])


def test_a_protected_top_level_directory_deleted_in_its_entirety_is_a_hard_tripwire(
    tmp_path: Path,
) -> None:
    """Deleting EVERY tracked file under a protected directory trips the gate.

    A recursive ``git diff-tree`` reports blob entries only, so the removal of
    a whole directory arrives as one deleted blob path per tracked file. The
    real diff - not a synthetic entry - is what proves the finding fires.
    """
    repo_root = tmp_path / "repo"
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
    tmp_path: Path,
) -> None:
    """A PARTIAL deletion is not an entirety deletion and stays silent."""
    repo_root = tmp_path / "repo"
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
    tmp_path: Path,
) -> None:
    """A rename leaves the NEW path behind unless the rollback removes it.

    The rejected state is sealed, then the tree is restored. The renamed
    file's new path is in the proposal tree and absent from the target, so
    the rollback must remove it - otherwise the tree no longer equals the
    target and the run would judge a tree the review never approved.
    """
    repo_root = tmp_path / "repo"
    _init_repo(repo_root, {"keep.txt": "keep\n", "old_name.txt": "payload\n"})

    _git(repo_root, "mv", "old_name.txt", "new_name.txt")
    seal = seal_pending(repo_root, "run-rename", 1)

    rollback(repo_root, seal.ref)

    assert not (repo_root / "new_name.txt").exists()
    assert (repo_root / "old_name.txt").read_text(encoding="utf-8") == "payload\n"


def test_a_staged_secret_is_detected_by_content_in_a_real_diff(tmp_path: Path) -> None:
    """A credential in the diff CONTENT is reported, not only its file name.

    The content scan only works when the diff carries the blob text, so
    this proof drives the real diff of a real repository.
    """
    repo_root = tmp_path / "repo"
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


def test_a_path_outside_the_configured_allowed_directories_is_reported(tmp_path: Path) -> None:
    """A configured ``allowed_directories`` scopes the review."""
    repo_root = tmp_path / "repo"
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

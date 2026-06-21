"""Black-box tests for ``auto_seed_default_git_exclude`` in ``ralph.config.bootstrap``.

The function must be idempotent, preserve user-added lines, cover the
canonical agent-internal inventory, and gracefully handle a non-git working
tree. All tests use ``tmp_path`` for I/O.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.config.bootstrap import (
    _DEFAULT_GIT_EXCLUDE_PATTERNS,
    auto_seed_default_git_exclude,
)

if TYPE_CHECKING:
    from pathlib import Path


def test_auto_seed_default_git_exclude_creates_exclude_when_missing(tmp_path: Path) -> None:
    """Fresh tmp_path with no ``.git/``: helper creates the dirs and seeds every default pattern."""
    repo_root = tmp_path / "fake_repo"
    repo_root.mkdir()
    assert not (repo_root / ".git" / "info" / "exclude").exists()

    appended = auto_seed_default_git_exclude(repo_root)

    exclude_path = repo_root / ".git" / "info" / "exclude"
    assert exclude_path.exists(), ".git/info/exclude must be created"
    content_lines = exclude_path.read_text(encoding="utf-8").splitlines()
    for pattern in _DEFAULT_GIT_EXCLUDE_PATTERNS:
        assert pattern in content_lines, f"Missing default pattern: {pattern!r}"
    assert appended == list(_DEFAULT_GIT_EXCLUDE_PATTERNS)


def test_auto_seed_default_git_exclude_is_idempotent(tmp_path: Path) -> None:
    """Calling the helper twice returns ``[]`` on the second call and does not duplicate."""
    repo_root = tmp_path / "fake_repo"
    repo_root.mkdir()
    auto_seed_default_git_exclude(repo_root)

    appended = auto_seed_default_git_exclude(repo_root)

    assert appended == [], f"Expected empty list on idempotent call, got {appended!r}"
    lines = set((repo_root / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines())
    for pattern in _DEFAULT_GIT_EXCLUDE_PATTERNS:
        assert pattern in lines, f"Pattern {pattern!r} missing"
        assert sum(1 for line in lines if line == pattern) == 1, f"Pattern {pattern!r} duplicated"


def test_auto_seed_default_git_exclude_appends_only_missing_patterns(tmp_path: Path) -> None:
    """Pre-seeded patterns are NOT re-appended -- only the missing ones are added."""
    repo_root = tmp_path / "fake_repo"
    repo_root.mkdir()
    exclude_path = repo_root / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    seed_pattern = _DEFAULT_GIT_EXCLUDE_PATTERNS[0]
    exclude_path.write_text(seed_pattern + "\n", encoding="utf-8")

    appended = auto_seed_default_git_exclude(repo_root)

    assert seed_pattern not in appended, "Already-present pattern must not be re-appended"
    assert appended == [p for p in _DEFAULT_GIT_EXCLUDE_PATTERNS if p != seed_pattern]


def test_auto_seed_default_git_exclude_preserves_user_added_entries(tmp_path: Path) -> None:
    """User-added lines survive the auto-seed and the full default set is added below them."""
    repo_root = tmp_path / "fake_repo"
    repo_root.mkdir()
    exclude_path = repo_root / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    user_line = "# user entry"
    seed_block = "\n".join((user_line, *_DEFAULT_GIT_EXCLUDE_PATTERNS))
    exclude_path.write_text(seed_block + "\n", encoding="utf-8")

    appended = auto_seed_default_git_exclude(repo_root)

    assert appended == []
    content_lines = exclude_path.read_text(encoding="utf-8").splitlines()
    assert content_lines[0] == user_line, (
        f"User-added first line must be preserved; got: {content_lines[:3]!r}"
    )
    for pattern in _DEFAULT_GIT_EXCLUDE_PATTERNS:
        assert pattern in content_lines, f"Missing default pattern: {pattern!r}"


def test_auto_seed_default_git_exclude_handles_missing_git_directory(tmp_path: Path) -> None:
    """Without ``.git/``, the helper creates the parents and writes the file.

    This covers the "Ralph invoked in a non-git project" case -- the helper
    must not raise. It also covers the bootstrap case where ``.git/`` was
    just initialized.
    """
    repo_root = tmp_path / "no_git"
    repo_root.mkdir()
    assert not (repo_root / ".git").exists()

    appended = auto_seed_default_git_exclude(repo_root)

    assert appended == list(_DEFAULT_GIT_EXCLUDE_PATTERNS)
    assert (repo_root / ".git" / "info" / "exclude").exists()

from __future__ import annotations

from pathlib import Path

import pytest
from git import Repo

from ralph.config import bootstrap as bs_module
from ralph.config.bootstrap import (
    _DEFAULT_GIT_EXCLUDE_PATTERNS,
    _resolve_git_exclude_path,
    auto_seed_default_git_exclude,
)

pytestmark = pytest.mark.timeout_seconds(5)


def test_auto_seed_default_git_exclude_creates_exclude_when_missing(tmp_path: Path) -> None:
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


def test_default_excludes_cover_auto_integrate_crash_record() -> None:
    assert ".agent/auto_integrate_in_progress.json" in _DEFAULT_GIT_EXCLUDE_PATTERNS


def test_auto_seed_default_git_exclude_is_idempotent(tmp_path: Path) -> None:
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
    repo_root = tmp_path / "no_git"
    repo_root.mkdir()
    assert not (repo_root / ".git").exists()

    appended = auto_seed_default_git_exclude(repo_root)

    assert appended == list(_DEFAULT_GIT_EXCLUDE_PATTERNS)
    assert (repo_root / ".git" / "info" / "exclude").exists()


def test_resolve_git_exclude_path_returns_none_for_non_git_directory(tmp_path: Path) -> None:
    repo_root = tmp_path / "no_git"
    repo_root.mkdir()
    assert _resolve_git_exclude_path(repo_root) is None


def test_resolve_git_exclude_path_returns_real_gitdir_in_worktree(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    worktree = _make_main_repo_and_worktree(tmp_git_repo, tmp_path)

    assert (worktree / ".git").is_file(), (
        f"Worktree .git must be a file, got: {list(worktree.iterdir())!r}"
    )

    resolved = _resolve_git_exclude_path(worktree)
    assert resolved is not None, "Resolver must find the real gitdir in a worktree"
    assert not str(resolved).startswith(str(worktree) + "/.git/info"), (
        f"Resolver returned the invalid worktree-root path: {resolved}"
    )
    assert str(resolved).endswith("/info/exclude"), f"Got: {resolved!r}"


def test_auto_seed_default_git_exclude_works_in_git_worktree(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    worktree = _make_main_repo_and_worktree(tmp_git_repo, tmp_path)

    assert (worktree / ".git").is_file()

    appended = auto_seed_default_git_exclude(worktree)
    assert appended == list(_DEFAULT_GIT_EXCLUDE_PATTERNS), (
        f"Expected full default pattern set, got {len(appended)} patterns"
    )

    invalid_path = worktree / ".git" / "info" / "exclude"
    assert not invalid_path.exists(), (
        f"Exclude file landed at the invalid worktree path: {invalid_path}"
    )

    gitfile_content = (worktree / ".git").read_text(encoding="utf-8")
    assert gitfile_content.startswith("gitdir:"), f"Expected a gitfile, got: {gitfile_content!r}"
    real_gitdir = Path(gitfile_content.split(":", 1)[1].strip())
    if not real_gitdir.is_absolute():
        real_gitdir = (worktree / real_gitdir).resolve()
    real_exclude = real_gitdir / "info" / "exclude"
    assert real_exclude.exists(), (
        f"Exclude file must be written to the real gitdir, expected: {real_exclude}"
    )
    content_lines = real_exclude.read_text(encoding="utf-8").splitlines()
    for pattern in _DEFAULT_GIT_EXCLUDE_PATTERNS:
        assert pattern in content_lines, f"Missing default pattern: {pattern!r}"


def test_auto_seed_default_git_exclude_is_idempotent_in_worktree(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    worktree = _make_main_repo_and_worktree(tmp_git_repo, tmp_path)

    first = auto_seed_default_git_exclude(worktree)
    assert first == list(_DEFAULT_GIT_EXCLUDE_PATTERNS)
    second = auto_seed_default_git_exclude(worktree)
    assert second == [], f"Second call must be a no-op in a worktree, got {second!r}"


def test_auto_seed_default_git_exclude_uses_atomic_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo_root = tmp_path / "fake_repo"
    repo_root.mkdir()
    captured: list[tuple[Path, str]] = []
    real_atomic = bs_module._atomic_append_text

    def spy_atomic(path: Path, payload: str, *, encoding: str = "utf-8") -> None:
        captured.append((path, payload))
        return real_atomic(path, payload, encoding=encoding)

    monkeypatch.setattr(bs_module, "_atomic_append_text", spy_atomic)

    auto_seed_default_git_exclude(repo_root)

    assert captured, "auto_seed_default_git_exclude must route through _atomic_append_text"
    exclude_path = repo_root / ".git" / "info" / "exclude"
    assert any(p == exclude_path for p, _ in captured), (
        f"Atomic helper must be called with the exclude file path; "
        f"observed paths: {[p for p, _ in captured]!r}"
    )


def _make_main_repo_and_worktree(main_repo: Path, tmp_path: Path) -> Path:
    worktree = tmp_path / "wt"
    main = Repo(main_repo)
    try:
        main.git.worktree("add", str(worktree), "HEAD")
    finally:
        main.close()
    return worktree

"""Black-box tests for ``auto_seed_default_git_exclude`` in ``ralph.config.bootstrap``.

The function must be idempotent, preserve user-added lines, cover the
canonical agent-internal inventory, gracefully handle a non-git working
tree, AND correctly resolve the real ``.git`` directory in git worktrees
and separate-git-dir layouts where ``repo_root/.git`` is a *file*
pointing at the real gitdir. All tests use ``tmp_path`` for I/O.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

from git import Repo

from ralph.config import bootstrap as bs_module
from ralph.config.bootstrap import (
    _DEFAULT_GIT_EXCLUDE_PATTERNS,
    _resolve_git_exclude_path,
    auto_seed_default_git_exclude,
)

if TYPE_CHECKING:
    import pytest


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


def test_resolve_git_exclude_path_returns_none_for_non_git_directory(tmp_path: Path) -> None:
    """Non-git working tree: resolver returns ``None`` (caller falls back to plain path)."""
    repo_root = tmp_path / "no_git"
    repo_root.mkdir()
    assert _resolve_git_exclude_path(repo_root) is None


def test_resolve_git_exclude_path_returns_real_gitdir_in_worktree(tmp_path: Path) -> None:
    """In a worktree the top-level ``.git`` is a gitfile -- the resolver must find the real gitdir.

    Regression for the worktree bug where the previous implementation
    called ``mkdir`` on the gitfile and failed with ``NotADirectoryError``.
    """
    worktree = _make_main_repo_and_worktree(tmp_path)

    # ``.git`` in the worktree is a file, not a directory.
    assert (worktree / ".git").is_file(), (
        f"Worktree .git must be a file, got: {list(worktree.iterdir())!r}"
    )

    resolved = _resolve_git_exclude_path(worktree)
    assert resolved is not None, "Resolver must find the real gitdir in a worktree"
    # Resolved path must NOT be ``<worktree>/.git/info/exclude`` (which is invalid).
    assert not str(resolved).startswith(str(worktree) + "/.git/info"), (
        f"Resolver returned the invalid worktree-root path: {resolved}"
    )
    # Resolved path must end with ``info/exclude`` and live under the real gitdir.
    assert str(resolved).endswith("/info/exclude"), f"Got: {resolved!r}"


def test_auto_seed_default_git_exclude_works_in_git_worktree(tmp_path: Path) -> None:
    """Regression test: ``auto_seed_default_git_exclude`` succeeds in a real git worktree.

    The previous implementation built ``repo_root / '.git' / 'info' / 'exclude'``
    and called ``mkdir()`` on it. In a worktree ``.git`` is a file
    (``gitdir: <real-gitdir>``), so the mkdir failed with
    ``NotADirectoryError``. The fix uses ``Repo(repo_root).git_dir`` to
    resolve the real gitdir.
    """
    worktree = _make_main_repo_and_worktree(tmp_path)

    # Sanity: ``.git`` is a gitfile in the worktree.
    assert (worktree / ".git").is_file()

    # The call must not raise.
    appended = auto_seed_default_git_exclude(worktree)
    assert appended == list(_DEFAULT_GIT_EXCLUDE_PATTERNS), (
        f"Expected full default pattern set, got {len(appended)} patterns"
    )

    # The exclude file must live in the real gitdir, NOT under
    # ``<worktree>/.git/info/exclude`` (which is invalid for a worktree).
    invalid_path = worktree / ".git" / "info" / "exclude"
    assert not invalid_path.exists(), (
        f"Exclude file landed at the invalid worktree path: {invalid_path}"
    )

    # The real gitdir is referenced by the gitfile -- resolve it and check.
    gitfile_content = (worktree / ".git").read_text(encoding="utf-8")
    assert gitfile_content.startswith("gitdir:"), (
        f"Expected a gitfile, got: {gitfile_content!r}"
    )
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


def test_auto_seed_default_git_exclude_is_idempotent_in_worktree(tmp_path: Path) -> None:
    """Idempotency works in a worktree: second call returns ``[]`` and does not duplicate."""
    worktree = _make_main_repo_and_worktree(tmp_path)

    first = auto_seed_default_git_exclude(worktree)
    assert first == list(_DEFAULT_GIT_EXCLUDE_PATTERNS)
    second = auto_seed_default_git_exclude(worktree)
    assert second == [], f"Second call must be a no-op in a worktree, got {second!r}"


def test_auto_seed_default_git_exclude_uses_atomic_helper(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``auto_seed_default_git_exclude`` must publish via the atomic helper.

    Regression: the previous implementation used a non-atomic
    ``open('a')`` write that could truncate or corrupt the exclude file
    on SIGKILL. The hardening routes the new payload through the
    sibling-staging atomic helper.
    """
    repo_root = tmp_path / "fake_repo"
    repo_root.mkdir()
    captured: list[tuple[Path, str]] = []
    real_atomic = bs_module._atomic_append_text

    def spy_atomic(path: Path, payload: str, *, encoding: str = "utf-8") -> None:
        captured.append((path, payload))
        return real_atomic(path, payload, encoding=encoding)

    monkeypatch.setattr(bs_module, "_atomic_append_text", spy_atomic)

    auto_seed_default_git_exclude(repo_root)

    assert captured, (
        "auto_seed_default_git_exclude must route through _atomic_append_text"
    )
    exclude_path = repo_root / ".git" / "info" / "exclude"
    assert any(p == exclude_path for p, _ in captured), (
        f"Atomic helper must be called with the exclude file path; "
        f"observed paths: {[p for p, _ in captured]!r}"
    )


def _make_main_repo_and_worktree(tmp_path: Path) -> Path:
    """Helper: create a main git repo with one commit and a linked worktree.

    Returns the worktree path (inside ``tmp_path``). The function
    configures a local user identity so the initial commit succeeds
    without a global git config.
    """
    main_repo = tmp_path / "main"
    main_repo.mkdir()
    main = Repo.init(main_repo)
    try:
        writer = main.config_writer()
        try:
            writer.set_value("user", "name", "Test User")
            writer.set_value("user", "email", "test@example.com")
        finally:
            writer.release()
        (main_repo / "README.md").write_text("hello", encoding="utf-8")
        main.index.add(["README.md"])
        main.index.commit("init")
    finally:
        main.close()

    worktree = tmp_path / "wt"
    worktree.mkdir()
    main2 = Repo(main_repo)
    try:
        main2.git.worktree("add", str(worktree), "HEAD")
    finally:
        main2.close()
    # ``main_repo`` is required as the source of the worktree; the
    # caller only needs the worktree path itself.
    return worktree

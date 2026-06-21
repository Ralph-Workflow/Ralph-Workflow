"""Unit tests for git commit cleanup operations."""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest
from git import Repo

from ralph.git import commit_cleanup as cc_module
from ralph.git import operations as ops
from ralph.git.commit_cleanup import (
    add_to_git_exclude,
    delete_file_from_repo,
    ensure_git_initialized,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


def test_delete_file_from_repo_removes_untracked_file(tmp_git_repo: Path) -> None:
    """Test that delete_file_from_repo removes an untracked file."""
    binary = tmp_git_repo / "binary.exe"
    binary.write_text("binary content")

    assert binary.exists()

    delete_file_from_repo(tmp_git_repo, "binary.exe")

    assert not binary.exists()


def test_delete_file_from_repo_unstages_staged_file(tmp_git_repo: Path) -> None:
    """Test that delete_file_from_repo unstages and removes a staged file."""
    binary = tmp_git_repo / "staged.bin"
    binary.write_text("staged content")

    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["staged.bin"])
        repo.index.commit("add staged file")

        # Modify after commit
        binary.write_text("modified content")

        delete_file_from_repo(tmp_git_repo, "staged.bin")

        assert not binary.exists()
        # Verify file is no longer tracked in the index
        ls_files = repo.git.ls_files("--stage", "staged.bin")
        assert ls_files.strip() == ""
    finally:
        repo.close()


def test_delete_file_from_repo_ignores_nonexistent_path(tmp_git_repo: Path) -> None:
    """Test that delete_file_from_repo does not raise for nonexistent files."""
    # Should not raise
    delete_file_from_repo(tmp_git_repo, "nonexistent.file")
    delete_file_from_repo(tmp_git_repo, "path/does/not/exist")


def test_add_to_git_exclude_creates_file_if_missing(tmp_git_repo: Path) -> None:
    """Test that add_to_git_exclude creates .git/info/exclude if missing."""
    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"

    add_to_git_exclude(tmp_git_repo, ["*.pyc"])

    assert exclude_path.exists()
    content = exclude_path.read_text()
    assert "*.pyc" in content


def test_add_to_git_exclude_appends_new_patterns(tmp_git_repo: Path) -> None:
    """Test that add_to_git_exclude appends patterns without removing existing ones."""
    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("existing pattern\n")

    add_to_git_exclude(tmp_git_repo, ["*.pyc", "*.log"])

    content = exclude_path.read_text()
    assert "existing pattern" in content
    assert "*.pyc" in content
    assert "*.log" in content


def test_add_to_git_exclude_deduplicates_patterns(tmp_git_repo: Path) -> None:
    """Test that add_to_git_exclude does not add duplicate patterns."""
    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("*.pyc\n")

    add_to_git_exclude(tmp_git_repo, ["*.pyc"])

    lines = [line for line in exclude_path.read_text().splitlines() if line]
    assert lines.count("*.pyc") == 1


def test_atomic_append_text_writes_via_path_replace(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Atomic helper writes via sibling staging + ``Path.replace``.

    Mirrors the pattern from ``ralph/mcp/transport/agy.py:99-127`` so
    SIGKILL mid-write leaves the target file intact.
    """
    target = tmp_git_repo / "atomic_target.txt"
    target.write_text("initial\n", encoding="utf-8")

    replace_calls: list[tuple[Path, Path]] = []

    real_replace = type(target).replace

    def spy_replace(self: Path, target_path: Path) -> Path:
        replace_calls.append((self, target_path))
        return real_replace(self, target_path)

    monkeypatch.setattr(type(target), "replace", spy_replace)

    ops._atomic_append_text(target, "extra\n", encoding="utf-8")

    assert replace_calls, "Path.replace must have been invoked for atomic publish"
    staging_path, dst = replace_calls[-1]
    assert dst == target, "Staging file must replace the target path"
    assert staging_path != target, (
        "Staging path must be a sibling file (with .ralph-staging.* suffix), "
        f"not the target itself (got: {staging_path})"
    )
    assert not staging_path.exists(), (
        "Staging file must not dangle after a successful atomic publish"
    )
    assert target.read_text(encoding="utf-8") == "initial\nextra\n"


def test_atomic_append_text_fails_closed_on_unreadable_existing_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the existing file cannot be read, the helper must NOT clobber it.

    Regression for the analysis decision ``how_to_fix`` item: the previous
    implementation caught ``OSError`` from ``Path.read_text`` and silently
    treated the file as empty, then published ``existing + payload`` via
    ``Path.replace()``. A transient read failure (permission denied,
    broken FS, transient I/O error) would therefore CLOBBER the original
    content with just the appended payload -- exactly the kind of silent
    corruption the atomic helper exists to prevent.

    The fix is fail-closed: when ``path.exists()`` is True but the read
    raises ``OSError``, the helper re-raises so the caller can decide.
    A missing file is still treated as empty (legitimate creation case).
    """
    target = tmp_path / "gitignore_existing.txt"
    target.write_text("EXISTING-CONTENT\n", encoding="utf-8")
    pre_bytes = target.read_bytes()

    fail_read = True
    real_read_text = type(target).read_text

    def fake_read_text(self: Path, *args: object, **kwargs: object) -> str:
        if self == target and fail_read:
            raise PermissionError("simulated read failure")
        return real_read_text(self, *args, **kwargs)

    monkeypatch.setattr(type(target), "read_text", fake_read_text)

    with pytest.raises(OSError):
        ops._atomic_append_text(target, "NEW\n", encoding="utf-8")

    fail_read = False
    assert target.read_bytes() == pre_bytes, (
        "Existing content must be preserved when read fails (no clobber). "
        f"Expected: {pre_bytes!r}, got: {target.read_bytes()!r}"
    )


def _publish_atomic_helper(_repo_root: Path, target: Path, _pattern: str) -> None:
    ops._atomic_append_text(target, "*.cache\n")


def _publish_append_to_gitignore(repo_root: Path, _target: Path, pattern: str) -> None:
    ops.append_to_gitignore(repo_root, [pattern])


def _publish_add_to_git_exclude(repo_root: Path, _target: Path, pattern: str) -> None:
    add_to_git_exclude(repo_root, [pattern])


@pytest.mark.parametrize(
    ("target_subpath", "publish_callable"),
    [
        pytest.param(
            "gitignore_no_newline.txt",
            _publish_atomic_helper,
            id="atomic-helper-direct",
        ),
        pytest.param(
            ".gitignore",
            _publish_append_to_gitignore,
            id="append_to_gitignore",
        ),
        pytest.param(
            ".git/info/exclude",
            _publish_add_to_git_exclude,
            id="add_to_git_exclude",
        ),
    ],
)
def test_atomic_append_text_separator_normalization(
    tmp_git_repo: Path,
    target_subpath: str,
    publish_callable: Callable[[Path, Path, str], object],
) -> None:
    """Boundary normalization: an existing file lacking a trailing newline gets one inserted.

    Regression for the analysis decision ``how_to_fix`` item: the previous
    implementation concatenated ``existing + payload`` without normalizing
    the boundary. For a file containing ``"existing-without-newline"`` and
    a payload of ``"*.cache\\n"`` the published result was
    ``"existing-without-newline*.cache\\n"`` -- a malformed single-line
    ignore rule rather than two separate rules.

    Parametrized across the three production callers
    (``_atomic_append_text`` directly, ``append_to_gitignore``, and
    ``add_to_git_exclude``) to verify boundary normalization is applied
    at every layer, not just at the helper level.
    """
    target = tmp_git_repo / target_subpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("existing-without-newline", encoding="utf-8")

    publish_callable(tmp_git_repo, target, "*.cache")

    raw = target.read_text(encoding="utf-8")
    assert "existing-without-newline\n*.cache" in raw, (
        f"Boundary must contain a newline so the two rules are separate lines; "
        f"got: {raw!r}"
    )


def test_atomic_append_text_cleans_sibling_on_exception(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """When the write raises mid-call, the sibling staging file must be cleaned.

    The helper must not leave a dangling staging file when the publish
    step fails. Otherwise a SIGKILL or disk-full during cleanup could
    silently corrupt subsequent invocations.
    """
    target = tmp_git_repo / "atomic_target2.txt"
    target.write_text("initial\n", encoding="utf-8")

    staging_seen: list[Path] = []
    real_unlink = type(target).unlink

    def spy_unlink(self: Path) -> None:
        staging_seen.append(self)
        real_unlink(self)

    def fake_write_text(self: Path, *args: object, **kwargs: object) -> int:
        # Raise immediately so the replace() call never runs; we then
        # verify the unlink() cleanup is attempted on the staging path.
        del args, kwargs
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(type(target), "write_text", fake_write_text)
    monkeypatch.setattr(type(target), "unlink", spy_unlink)

    with pytest.raises(OSError, match="simulated mid-write failure"):
        ops._atomic_append_text(target, "extra\n", encoding="utf-8")

    assert staging_seen, (
        "Helper must call staging.unlink() after a failed write_text"
    )
    assert target.read_text(encoding="utf-8") == "initial\n", (
        "Target file must NOT be modified when the publish step fails"
    )


def test_add_to_git_exclude_routes_through_atomic_helper(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``add_to_git_exclude`` must call the atomic helper for the new payload.

    Defense-in-depth: the previous implementation used a non-atomic
    ``open('a')`` write that could truncate or corrupt the file on
    SIGKILL mid-write. The hardening routes the new payload through
    ``_atomic_append_text`` so a SIGKILL during cleanup leaves the
    exclude file intact.
    """
    captured: list[tuple[Path, str]] = []
    real_atomic = cc_module._atomic_append_text

    def spy_atomic(path: Path, payload: str, *, encoding: str = "utf-8") -> None:
        captured.append((path, payload))
        return real_atomic(path, payload, encoding=encoding)

    monkeypatch.setattr(cc_module, "_atomic_append_text", spy_atomic)

    add_to_git_exclude(tmp_git_repo, [".env.local"])

    assert captured, "add_to_git_exclude must route through _atomic_append_text"
    written_path, written_payload = captured[-1]
    assert ".env.local" in written_payload, (
        f"Payload must include the new pattern, got: {written_payload!r}"
    )
    exclude_file = tmp_git_repo / ".git" / "info" / "exclude"
    assert written_path == exclude_file, (
        f"Atomic helper must be called with the exclude file path, "
        f"got: {written_path}"
    )


def test_append_to_gitignore_routes_through_atomic_helper(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """``append_to_gitignore`` must call the atomic helper for the new payload."""
    captured: list[tuple[Path, str]] = []
    real_atomic = ops._atomic_append_text

    def spy_atomic(path: Path, payload: str, *, encoding: str = "utf-8") -> None:
        captured.append((path, payload))
        return real_atomic(path, payload, encoding=encoding)

    monkeypatch.setattr(ops, "_atomic_append_text", spy_atomic)

    ops.append_to_gitignore(tmp_git_repo, ["*.cache"])

    assert captured, "append_to_gitignore must route through _atomic_append_text"
    written_path, written_payload = captured[-1]
    assert "*.cache" in written_payload, (
        f"Payload must include the new pattern, got: {written_payload!r}"
    )
    assert written_path == tmp_git_repo / ".gitignore", (
        f"Atomic helper must be called with the .gitignore path, "
        f"got: {written_path}"
    )


def test_delete_file_from_repo_rejects_symlink_pointing_to_sibling_inside_repo(
    tmp_git_repo: Path,
) -> None:
    """Symlink at a deletable path that points to a sibling regular file must be rejected.

    The symlink check MUST run on the unresolved target -- ``Path.resolve()``
    follows symlinks, so a symlink at ``evil_link`` resolves to its sibling
    and a post-resolve ``is_symlink()`` check would not fire on the symlink
    itself. The cleanup helper must reject the symlink path before the
    resolve, so the sibling file is preserved.
    """
    sibling = tmp_git_repo / "sibling.txt"
    sibling.write_text("I am the sibling target\n")
    evil_link = tmp_git_repo / "evil_link"
    evil_link.symlink_to(sibling)

    with pytest.raises(ValueError, match="symlink"):
        delete_file_from_repo(tmp_git_repo, "evil_link")

    assert evil_link.is_symlink(), "The symlink itself must be preserved"
    assert sibling.exists(), "The sibling file must NOT be deleted as a side effect"


def test_delete_file_from_repo_rejects_symlink_pointing_outside_repo(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """Symlink that escapes the repo root via a target outside must be rejected.

    Defense-in-depth: the earlier ``is_absolute()`` / ``..`` segment check
    on the literal path string catches most escape attempts, but a symlink
    whose literal path is inside the repo can still resolve outside. The
    pre-resolve symlink check plus the post-resolve ``relative_to`` check
    together cover both vectors.
    """
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the repo\n")
    escape_link = tmp_git_repo / "escape_link"
    escape_link.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        delete_file_from_repo(tmp_git_repo, "escape_link")

    assert escape_link.is_symlink(), "The escape symlink itself must be preserved"
    assert outside.exists(), "The outside file must NOT be deleted"


def test_delete_file_from_repo_rejects_broken_symlink_inside_repo(tmp_git_repo: Path) -> None:
    """A broken symlink (target missing) must also be rejected as a symlink.

    The post-resolve ``exists()`` check would fall through silently for a
    broken symlink (since the target is missing). The pre-resolve symlink
    check catches the case before the resolve.
    """
    broken_link = tmp_git_repo / "broken_link"
    broken_link.symlink_to(tmp_git_repo / "nonexistent_target")

    with pytest.raises(ValueError, match="symlink"):
        delete_file_from_repo(tmp_git_repo, "broken_link")

    assert broken_link.is_symlink(), "The broken symlink itself must be preserved"


def test_delete_file_from_repo_propagates_oserror_on_unlink_failure(
    tmp_git_repo: Path,
) -> None:
    """A failed ``Path.unlink`` call must propagate ``OSError`` to the caller.

    The current implementation swallows ``OSError`` via
    ``with suppress(OSError):`` -- a real delete failure would log success
    even when nothing was deleted. The hardening removes the suppression
    so ``OSError`` propagates to the caller's WARNING log.

    Deterministic failure injection: create a DIRECTORY (not a file) at the
    target path. ``Path.unlink(missing_ok=True)`` on a directory raises
    ``OSError`` (Errno 21 ``IsADirectoryError`` on POSIX, Errno 5
    ``PermissionError`` on Windows -- both subclass ``OSError``).
    """
    undeletable = tmp_git_repo / "undeletable_dir"
    undeletable.mkdir()

    assert undeletable.is_dir()
    with pytest.raises(OSError):
        delete_file_from_repo(tmp_git_repo, "undeletable_dir")

    assert undeletable.is_dir(), "The directory must NOT be deleted after the failed call"


def test_ensure_git_initialized_inits_non_repo_directory(tmp_path: Path) -> None:
    """Test that ensure_git_initialized initializes a non-git directory."""
    non_repo = tmp_path / "non_repo"
    non_repo.mkdir()

    assert not (non_repo / ".git").exists()

    ensure_git_initialized(non_repo)

    assert (non_repo / ".git").exists()
    # Verify it's a valid git repo
    with Repo(non_repo) as repo:
        assert repo.active_branch.name in ("main", "master", "HEAD")


def test_ensure_git_initialized_is_noop_for_existing_repo(tmp_git_repo: Path) -> None:
    """Test that ensure_git_initialized does nothing for existing repos."""
    # Should not raise
    ensure_git_initialized(tmp_git_repo)

    # Repo should still be valid
    with Repo(tmp_git_repo) as repo:
        assert repo.active_branch.name in ("main", "master", "HEAD")
        # Commit should still exist
        assert repo.head.commit.hexsha

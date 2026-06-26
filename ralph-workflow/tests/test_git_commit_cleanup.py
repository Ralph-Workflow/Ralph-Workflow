"""Unit tests for git commit cleanup operations."""

from __future__ import annotations

import shutil
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest
from git import GitCommandError, Repo
from loguru import logger

from ralph.git import commit_cleanup as cc_module
from ralph.git import operations as ops
from ralph.git.commit_cleanup import (
    add_to_git_exclude,
    delete_file_from_repo,
    ensure_git_initialized,
    untrack_engine_internal_files,
)
from ralph.git.operations import get_head_sha

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
    implementation caught ``OSError`` from ``Path.read_bytes`` and silently
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
    real_read_bytes = type(target).read_bytes

    def fake_read_bytes(self: Path) -> bytes:
        if self == target and fail_read:
            raise PermissionError("simulated read failure")
        return real_read_bytes(self)

    monkeypatch.setattr(type(target), "read_bytes", fake_read_bytes)

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

    def fake_write_bytes(self: Path, data: bytes) -> int:
        # Raise immediately so the replace() call never runs; we then
        # verify the unlink() cleanup is attempted on the staging path.
        del data
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(type(target), "write_bytes", fake_write_bytes)
    monkeypatch.setattr(type(target), "unlink", spy_unlink)

    with pytest.raises(OSError, match="simulated mid-write failure"):
        ops._atomic_append_text(target, "extra\n", encoding="utf-8")

    assert staging_seen, (
        "Helper must call staging.unlink() after a failed write_bytes"
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


# --- Phase 4 edge-case tests for delete_file_from_repo ---
#
# Each test pins one observable behavior of the cleanup helper. The names
# follow the plan: test_<unit>_<behavior> so the test id matches the
# failing-commit-phase symptoms the user reported.


@pytest.mark.timeout_seconds(5)
def test_delete_file_from_repo_handles_untracked_existing_file(tmp_git_repo: Path) -> None:
    """Untracked existing file at a deletable path is removed end-to-end.

    The helper must accept an existing-but-untracked file and remove it
    without raising. The git index step is a no-op (no index entries),
    but ``Path.unlink(missing_ok=True)`` must still remove the file from
    the worktree. A prior implementation gated the unlink on the
    ``tracked_in_index`` flag and silently skipped untracked files --
    the hardening makes the unlink unconditional.
    """
    binary = tmp_git_repo / "untracked.exe"
    binary.write_text("binary content")

    assert binary.exists()
    assert not binary.is_symlink()

    delete_file_from_repo(tmp_git_repo, "untracked.exe")

    assert not binary.exists(), (
        "Untracked existing file MUST be removed by delete_file_from_repo"
    )


@pytest.mark.timeout_seconds(5)
def test_delete_file_from_repo_handles_dot_slash_prefixed_path(tmp_git_repo: Path) -> None:
    """A ``./`` prefix on the relative path must be normalized to the bare path.

    The cleanup helper accepts ``./binary.exe`` equivalently to
    ``binary.exe`` -- the ``PurePath`` constructor treats ``./`` as a no-op
    in the parts tuple, so the relative-to-repo check passes for both
    shapes. This pins the helper's tolerance for the prefix an agent may
    include when normalizing paths via os.path.relpath or similar.
    """
    binary = tmp_git_repo / "binary.exe"
    binary.write_text("binary content")

    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["binary.exe"])
        repo.index.commit("track binary")
    finally:
        repo.close()

    delete_file_from_repo(tmp_git_repo, "./binary.exe")

    assert not binary.exists(), (
        "File MUST be removed when the path uses a ``./`` prefix"
    )


@pytest.mark.timeout_seconds(5)
def test_delete_file_from_repo_rejects_symlinked_parent_dir(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
    """A path that traverses through a symlinked parent pointing outside is rejected.

    The unresolved-path ``is_symlink()`` check guards against a symlink at
    the literal path; the post-resolve ``relative_to`` guard catches a
    path that resolves through a symlinked parent to a location outside
    the repo root. With ``safe_alias`` pointing outside, calling
    ``delete_file_from_repo(tmp_git_repo, "safe_alias/foo.txt")`` would
    either resolve to ``<outside>/foo.txt`` (relative_to fails) or hit
    the unresolved-path symlink check when ``safe_alias`` is itself a
    symlink whose resolution escapes the repo.
    """
    outside = tmp_path / "outside"
    outside.mkdir()
    (outside / "foo.txt").write_text("outside content\n")

    safe_alias = tmp_git_repo / "safe_alias"
    safe_alias.symlink_to(outside)

    assert safe_alias.is_symlink()
    assert (safe_alias / "foo.txt").exists(), (
        "Setup invariant: the symlinked parent must currently expose foo.txt"
    )

    with pytest.raises(ValueError):
        delete_file_from_repo(tmp_git_repo, "safe_alias/foo.txt")

    assert (outside / "foo.txt").exists(), (
        "The outside file must NOT be deleted as a side effect"
    )


@pytest.mark.timeout_seconds(5)
def test_delete_file_from_repo_propagates_index_lock_error(tmp_git_repo: Path) -> None:
    """An existing ``.git/index.lock`` causes git operations to fail and the error propagates.

    The cleanup helper wraps the git index lookup in
    ``with suppress(InvalidGitRepositoryError):`` -- that suppression is
    intentionally narrow: it does NOT swallow ``GitCommandError``,
    ``OSError``, or any other error class. A stale ``index.lock`` will
    cause the underlying ``git rm -f --cached -- <path>`` (invoked via
    the ``Repo`` index lookup) to fail with ``GitCommandError``
    (``exit code 128`` and stderr ``Unable to create '.../index.lock':
    File exists.``), and that failure must propagate to the caller so
    the WARNING log surfaces the real cause instead of silently claiming
    success.

    The test pins the narrow exception contract: ``GitCommandError`` is
    the specific class that surfaces. The production helper does NOT
    swallow it via the ``InvalidGitRepositoryError`` suppression. The
    plan originally specified ``OSError`` as the assertion target, but
    GitPython wraps git-exit failures in ``GitCommandError`` (which is
    NOT an ``OSError`` subclass on any supported Python version). The
    narrower assertion is the right regression guard.
    """
    binary = tmp_git_repo / "binary.exe"
    binary.write_text("binary content")

    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["binary.exe"])
        repo.index.commit("track binary")
    finally:
        repo.close()

    # Simulate a concurrent git operation by writing the lock file.
    (tmp_git_repo / ".git" / "index.lock").write_text("locked", encoding="utf-8")
    assert (tmp_git_repo / ".git" / "index.lock").exists()

    try:
        with pytest.raises(GitCommandError) as excinfo:
            delete_file_from_repo(tmp_git_repo, "binary.exe")
        assert "index.lock" in str(excinfo.value) or "File exists" in str(excinfo.value), (
            f"Stderr must surface the index-lock conflict, got: {excinfo.value!s}"
        )
    finally:
        # Best-effort cleanup so tmp_git_repo can be reaped.
        with suppress(FileNotFoundError):
            (tmp_git_repo / ".git" / "index.lock").unlink()


@pytest.mark.timeout_seconds(5)
def test_delete_file_from_repo_accepts_str_path_argument(tmp_git_repo: Path) -> None:
    """Passing a ``str`` (not ``Path``) as ``repo_root`` is accepted without TypeError.

    The helper signature is ``def delete_file_from_repo(repo_root: Path | str, ...)``
    -- the first line normalizes via ``Path(repo_root).resolve()`` so a
    string path is equivalent to a Path. This pins that public contract
    so a future refactor that drops the ``Path()`` wrapper surfaces as a
    TypeError regression.
    """
    binary = tmp_git_repo / "binary.exe"
    binary.write_text("binary content")

    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["binary.exe"])
        repo.index.commit("track binary")
    finally:
        repo.close()

    str_repo_root = str(tmp_git_repo)
    assert isinstance(str_repo_root, str)

    delete_file_from_repo(str_repo_root, "binary.exe")

    assert not binary.exists(), (
        "File MUST be removed when repo_root is passed as str"
    )


# --- Phase 8 edge-case tests for ensure_git_initialized ---
#
# Each test pins one observable behavior of the git-init helper. The names
# follow the plan: test_ensure_git_initialized_<behavior> so the test id
# maps directly to the contract pinned by the existing
# ``test_ensure_git_initialized_*`` tests.


@pytest.mark.timeout_seconds(5)
def test_ensure_git_initialized_accepts_str_path_argument(tmp_path: Path) -> None:
    """Passing a ``str`` (not ``Path``) as ``repo_root`` initializes a non-git directory.

    The helper signature is ``def ensure_git_initialized(repo_root: Path | str) -> None``
    -- the implementation passes the value through to ``Repo(repo_root, ...)``
    which accepts both. This pins the public contract so a future refactor
    that drops the union-arg support surfaces as a TypeError regression.
    """
    non_repo = tmp_path / "non_repo_str"
    non_repo.mkdir()
    assert not (non_repo / ".git").exists()

    ensure_git_initialized(str(non_repo))

    assert (non_repo / ".git").exists()
    with Repo(non_repo) as repo:
        assert repo.active_branch.name in ("main", "master", "HEAD")


@pytest.mark.timeout_seconds(5)
def test_ensure_git_initialized_does_not_clobber_existing_repo(tmp_git_repo: Path) -> None:
    """Pre-existing repo: HEAD SHA is preserved across ``ensure_git_initialized``.

    Pins the no-clobber contract: when ``.git`` already points at a
    valid git repository, the helper must NOT re-initialize. The HEAD
    SHA is the simplest regression anchor -- a fresh ``Repo.init``
    would create a new ``.git/HEAD`` with no commits and the SHA check
    would fail.
    """
    head_sha_before = get_head_sha(tmp_git_repo)
    assert head_sha_before
    head_commit_before = Repo(tmp_git_repo).head.commit.hexsha

    ensure_git_initialized(tmp_git_repo)

    head_sha_after = get_head_sha(tmp_git_repo)
    assert head_sha_after == head_sha_before, (
        f"HEAD SHA must be preserved across ensure_git_initialized, "
        f"was {head_sha_before!r}, now {head_sha_after!r}"
    )
    assert Repo(tmp_git_repo).head.commit.hexsha == head_commit_before


@pytest.mark.timeout_seconds(5)
def test_ensure_git_initialized_handles_gitfile_layout_separate_git_dir(
    tmp_path: Path,
) -> None:
    """A gitfile layout (worktree pointing at a separate git dir) is accepted.

    The plan pins the separate-git-dir case so a worktree-style layout
    (where ``.git`` is a file containing ``gitdir: <path>``) does NOT
    crash the helper. ``Repo(repo_root)`` resolves the gitfile
    transparently and ``search_parent_directories=False`` keeps the
    helper scoped to the requested path.
    """
    # Build a separate-git-dir layout: a real git repo at
    # ``separate_dir`` plus a worktree directory whose ``.git`` is a
    # file pointing at ``separate_dir``.
    separate_dir = tmp_path / "separate"
    separate_dir.mkdir()
    Repo.init(separate_dir)
    assert (separate_dir / ".git").is_dir(), (
        "Setup invariant: separate_dir/.git must be a real git directory"
    )

    worktree = tmp_path / "worktree"
    worktree.mkdir()
    (worktree / ".git").write_text(f"gitdir: {separate_dir}/.git\n", encoding="utf-8")
    assert (worktree / ".git").is_file(), (
        "Setup invariant: worktree/.git must be a gitfile, not a directory"
    )

    # Must not raise.
    ensure_git_initialized(worktree)

    # The separate git dir is still valid.
    with Repo(separate_dir) as repo:
        assert repo.active_branch.name in ("main", "master", "HEAD")


@pytest.mark.timeout_seconds(5)
def test_ensure_git_initialized_raises_on_corrupt_git_dir(tmp_path: Path) -> None:
    """Corrupt ``.git`` raises ``GitCommandError`` (file is not a directory or gitfile).

    The helper wraps ``Repo(repo_root)`` in
    ``with suppress(InvalidGitRepositoryError):`` and falls back to
    ``Repo.init(repo_root)`` on the suppressed branch. With ``.git``
    pointing at a regular file (NOT a directory, NOT a gitfile with
    ``gitdir: ...``), the ``Repo(...)`` constructor sees a non-empty
    file as the ``.git`` path and falls through to ``Repo.init(...)``
    on the suppressed branch -- which then fails with
    ``GitCommandError`` (``exit code 128`` and stderr
    ``fatal: invalid gitfile format: <path>/.git``) because the
    existing file is not parseable as a gitfile.

    The test pins the narrow exception contract: ``GitCommandError`` is
    the specific class that surfaces. The corrupt ``.git`` file is
    preserved on disk (NOT silently overwritten by ``Repo.init`` --
    ``init`` errors out before mutating the directory).
    """
    corrupt = tmp_path / "corrupt_repo"
    corrupt.mkdir()
    (corrupt / ".git").write_text("not a git directory, not a gitfile\n", encoding="utf-8")
    assert (corrupt / ".git").is_file()

    with pytest.raises(GitCommandError) as excinfo:
        ensure_git_initialized(corrupt)
    assert "invalid gitfile format" in str(excinfo.value), (
        f"Stderr must surface the invalid-gitfile-format error, got: {excinfo.value!s}"
    )
    # The corrupt .git file must NOT have been silently re-initialized.
    assert (corrupt / ".git").is_file(), (
        "ensure_git_initialized must NOT silently re-init over a corrupt .git"
    )
    assert (corrupt / ".git").read_text(encoding="utf-8") == (
        "not a git directory, not a gitfile\n"
    ), (
        "Corrupt .git file content must NOT have been modified by a silent re-init"
    )


# ---------------------------------------------------------------------------
# Pre-emptive untrack helper tests (commit_cleanup phase safety net)
# ---------------------------------------------------------------------------
#
# These tests pin the helper that the commit_cleanup phase calls BEFORE
# the agent runs to remove tracked engine-internal files from the index.
# Without this safety net, the agent's delete_file action for tracked
# engine files is rejected ("Refusing to delete non-housekeeping file")
# even when the file is in the canonical engine allowlist, because the
# safety check happens AFTER the file was checked into HEAD.
#
# The helper takes an ``is_internal_path`` predicate as a positional
# argument (NOT imported) so it stays decoupled from the leaf module
# and has no circular-import risk. Tests pass a lambda that always
# returns True (matches all engine-internal paths) to exercise the
# full index walk, or a lambda that always returns False to pin the
# non-engine preservation contract.


def _track_and_commit(repo_root: Path, rel_path: str) -> None:
    """Stage and commit ``rel_path`` against ``repo_root``.

    Mirrors the helper used in ``tests/test_phases_commit_cleanup.py``
    so the pre-emptive untrack tests can verify what is and is not
    in ``git ls-files --cached`` after the helper runs.
    """
    repo = Repo(repo_root)
    try:
        repo.index.add([rel_path])
        repo.index.commit(f"track {rel_path}")
    finally:
        repo.close()


@pytest.mark.timeout_seconds(5)
def test_untrack_engine_internal_files_removes_tracked_engine_files(
    tmp_git_repo: Path,
) -> None:
    """Pre-emptive untrack: tracked engine files are removed from the index.

    Pins the contract that ``untrack_engine_internal_files`` runs
    ``git rm --cached`` (NOT ``git rm``) on every tracked path that
    matches the predicate. The working-tree files MAY still exist
    after the call -- only the INDEX entries are removed. The test
    asserts the three originally-failing tracked paths
    (``.agent/raw/opencode.log``, ``.agent/tmp/mcp-server.log``,
    ``checkpoint.json``) are NO LONGER in ``git ls-files --cached``
    AND are NO LONGER in ``Repo.index.entries``.
    """
    log_path = tmp_git_repo / ".agent" / "raw" / "opencode.log"
    mcp_log_path = tmp_git_repo / ".agent" / "tmp" / "mcp-server.log"
    checkpoint_path = tmp_git_repo / "checkpoint.json"
    log_path.parent.mkdir(parents=True, exist_ok=True)
    mcp_log_path.parent.mkdir(parents=True, exist_ok=True)
    log_path.write_text("log content")
    mcp_log_path.write_text("mcp log")
    checkpoint_path.write_text('{"phase": "development"}')

    _track_and_commit(tmp_git_repo, ".agent/raw/opencode.log")
    _track_and_commit(tmp_git_repo, ".agent/tmp/mcp-server.log")
    _track_and_commit(tmp_git_repo, "checkpoint.json")

    # Use a predicate that mirrors the canonical ``is_agent_internal_path``
    # allowlist: only engine-internal paths are untracked. A bare
    # ``lambda p: True`` would also untrack ``README.md`` from the
    # ``tmp_git_repo`` template fixture and obscure the contract this
    # test pins.
    def _engine_path(p: str) -> bool:
        return p in {
            ".agent/raw/opencode.log",
            ".agent/tmp/mcp-server.log",
            "checkpoint.json",
        }

    untracked = untrack_engine_internal_files(tmp_git_repo, _engine_path)

    assert set(untracked) == {
        ".agent/raw/opencode.log",
        ".agent/tmp/mcp-server.log",
        "checkpoint.json",
    }, f"untrack must report every tracked engine path it removed, got: {untracked!r}"

    repo = Repo(tmp_git_repo)
    try:
        cached = set(repo.git.ls_files("--cached").splitlines())
        assert ".agent/raw/opencode.log" not in cached, (
            "Tracked engine file MUST be removed from git ls-files --cached"
        )
        assert ".agent/tmp/mcp-server.log" not in cached, (
            "Tracked engine file MUST be removed from git ls-files --cached"
        )
        assert "checkpoint.json" not in cached, (
            "Tracked engine file MUST be removed from git ls-files --cached"
        )
        index_paths = {entry_path for entry_path, _stage in repo.index.entries}
        assert ".agent/raw/opencode.log" not in index_paths
        assert ".agent/tmp/mcp-server.log" not in index_paths
        assert "checkpoint.json" not in index_paths
    finally:
        repo.close()

    # Working-tree files MAY still exist -- the helper uses ``git rm --cached``,
    # not ``git rm``. Pin that contract so a future refactor that swaps to
    # ``git rm`` (which deletes the file from disk) surfaces as a regression.
    assert log_path.exists(), "Working-tree file must remain after ``git rm --cached``"
    assert mcp_log_path.exists(), "Working-tree file must remain after ``git rm --cached``"
    assert checkpoint_path.exists(), "Working-tree file must remain after ``git rm --cached``"


@pytest.mark.timeout_seconds(5)
def test_untrack_engine_internal_files_preserves_non_engine_files(
    tmp_git_repo: Path,
) -> None:
    """Non-engine tracked files are NOT removed when the predicate rejects them.

    Pins the safety boundary: only paths the predicate accepts are
    untracked. A predicate that always returns False leaves the index
    untouched -- the helper must NEVER widen the deletion surface on
    its own. The test pre-stages ``src/main.go`` and
    ``tests/test_foo.py`` and asserts both are STILL in
    ``git ls-files --cached`` after the helper runs.
    """
    src_path = tmp_git_repo / "src" / "main.go"
    src_path.parent.mkdir(parents=True, exist_ok=True)
    src_path.write_text("package main\n")
    test_path = tmp_git_repo / "tests" / "test_foo.py"
    test_path.parent.mkdir(parents=True, exist_ok=True)
    test_path.write_text("def test_foo():\n    pass\n")

    _track_and_commit(tmp_git_repo, "src/main.go")
    _track_and_commit(tmp_git_repo, "tests/test_foo.py")

    untracked = untrack_engine_internal_files(tmp_git_repo, lambda _p: False)

    assert untracked == [], (
        f"Helper must not untrack anything when predicate rejects every path, "
        f"got: {untracked!r}"
    )

    repo = Repo(tmp_git_repo)
    try:
        cached = set(repo.git.ls_files("--cached").splitlines())
        assert "src/main.go" in cached, (
            "Non-engine tracked file MUST remain in git ls-files --cached"
        )
        assert "tests/test_foo.py" in cached, (
            "Non-engine tracked file MUST remain in git ls-files --cached"
        )
    finally:
        repo.close()


@pytest.mark.timeout_seconds(5)
def test_untrack_engine_internal_files_rejects_symlinks(
    tmp_git_repo: Path,
) -> None:
    """A symlink under ``.agent/`` is rejected before any ``git rm --cached``.

    Pins the contract that the helper calls
    ``Path(repo_root / path).is_symlink()`` BEFORE issuing
    ``git rm --cached``. A symlink under ``.agent/`` could point
    outside the repo (git follows symlinks); the helper must skip
    the entry and log a WARNING, returning ``[]`` so the caller can
    record the symlink as deliberately-preserved.
    """
    target = tmp_git_repo / ".agent" / "raw" / "evil_link"
    target.parent.mkdir(parents=True, exist_ok=True)
    target.symlink_to(tmp_git_repo / "outside_target")

    _track_and_commit(tmp_git_repo, ".agent/raw/evil_link")

    repo = Repo(tmp_git_repo)
    try:
        assert ".agent/raw/evil_link" in {
            entry_path for entry_path, _stage in repo.index.entries
        }, "Setup invariant: symlink must be tracked before the untrack call"
    finally:
        repo.close()

    def _engine_path(p: str) -> bool:
        return p == ".agent/raw/evil_link"

    untracked = untrack_engine_internal_files(tmp_git_repo, _engine_path)

    assert untracked == [], (
        f"Symlink must be skipped by the helper, got: {untracked!r}"
    )

    repo = Repo(tmp_git_repo)
    try:
        cached = set(repo.git.ls_files("--cached").splitlines())
        assert ".agent/raw/evil_link" in cached, (
            "Symlink MUST remain in git ls-files --cached after a rejected untrack"
        )
    finally:
        repo.close()


@pytest.mark.timeout_seconds(5)
def test_untrack_engine_internal_files_handles_empty_index(
    tmp_git_repo: Path,
) -> None:
    """Fresh repo with no commits: helper returns ``[]`` without raising.

    Pins the empty-index edge case: ``tmp_git_repo`` has the initial
    commit from the template fixture, so the test pre-removes that
    commit by deleting HEAD and rebuilding the repo with no
    checkpoints. The helper must complete without raising and return
    an empty list -- a missing index entry must NOT crash the
    pre-emptive untrack step.
    """
    # Rebuild the repo with NO commits so the index is empty.
    shutil.rmtree(tmp_git_repo)
    tmp_git_repo.mkdir()
    Repo.init(tmp_git_repo)

    untracked = untrack_engine_internal_files(tmp_git_repo, lambda _p: True)

    assert untracked == [], (
        f"Helper must return [] for an empty index, got: {untracked!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_untrack_engine_internal_files_handles_non_git_directory(tmp_path: Path) -> None:
    """A non-git directory: helper returns ``[]`` without raising.

    Pins the broken-git-state edge case: the helper wraps
    ``Repo(repo_root)`` in a try/finally and suppresses construction
    failures so a workspace whose root is NOT a git repo does not
    stall the commit_cleanup phase. The caller (``handle_commit_cleanup_phase``)
    also wraps the helper in try/except, so a fail-closed return of
    ``[]`` is the canonical contract.
    """
    non_repo = tmp_path / "non_repo"
    non_repo.mkdir()
    assert not (non_repo / ".git").exists(), "Setup invariant: non_repo must not be a git repo"

    untracked = untrack_engine_internal_files(non_repo, lambda _p: True)

    assert untracked == [], (
        f"Helper must return [] for a non-git directory, got: {untracked!r}"
    )


@pytest.mark.timeout_seconds(5)
def test_untrack_engine_internal_files_skills_silent(
    tmp_git_repo: Path,
) -> None:
    """wt-025 / AC-03 leaf contract: tracked skill symlinks under the FIVE roots
    are early-skipped BEFORE the symlink-WARNING block, so the helper emits
    ZERO WARNING-level log lines when the only ``.agent/``-ish tracked paths
    are intentional project-scope skill symlinks.

    Pre-stages a tracked ``.agents/skills/<name>`` symlink (intentional by
    design; see commit ``e4b47d2fb``) and asserts:

      * the helper returns ``[]`` (the symlink is NOT untracked),
      * no WARNING-level log message was emitted, and
      * the symlink is STILL in ``git ls-files --cached`` (early-skip
        preserved the entry).
    """
    canonical_skill = tmp_git_repo / ".opencode" / "skills" / "brainstorming"
    canonical_skill.mkdir(parents=True, exist_ok=True)
    canonical_skill.joinpath("SKILL.md").write_text(
        "# brainstorming skill\n", encoding="utf-8"
    )
    sibling_skill = tmp_git_repo / ".agents" / "skills" / "brainstorming"
    sibling_skill.parent.mkdir(parents=True, exist_ok=True)
    sibling_skill.symlink_to(canonical_skill, target_is_directory=True)

    _track_and_commit(tmp_git_repo, ".opencode/skills/brainstorming/SKILL.md")
    _track_and_commit(tmp_git_repo, ".agents/skills/brainstorming")

    captured_warnings: list[str] = []
    sink_id = logger.add(
        captured_warnings.append, level="WARNING", format="{message}"
    )
    try:
        # Predicate accepts ONLY the .agents/skills/* path (which is the
        # one that would historically fire the symlink-WARNING). The test
        # pins that the skill-root early-skip runs BEFORE the predicate gate,
        # not after.
        def _engine_path(p: str) -> bool:
            return p == ".agents/skills/brainstorming"

        untracked = untrack_engine_internal_files(tmp_git_repo, _engine_path)
    finally:
        logger.remove(sink_id)

    assert untracked == [], (
        f"Skill symlinks must NOT be untracked; got: {untracked!r}"
    )
    assert captured_warnings == [], (
        f"No WARNING should be emitted for tracked skill-root paths; "
        f"got: {captured_warnings!r}"
    )

    repo = Repo(tmp_git_repo)
    try:
        cached = set(repo.git.ls_files("--cached").splitlines())
        assert ".agents/skills/brainstorming" in cached, (
            "Tracked skill-root symlink MUST remain in git ls-files --cached "
            "after the early-skip"
        )
    finally:
        repo.close()


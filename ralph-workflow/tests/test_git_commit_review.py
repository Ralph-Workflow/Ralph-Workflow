from __future__ import annotations

import shutil
from contextlib import suppress
from typing import TYPE_CHECKING

import pytest
from git import GitCommandError, Repo
from loguru import logger

from ralph.git import commit_review as cc_module
from ralph.git import operations as ops
from ralph.git.commit_review import (
    add_to_git_exclude,
    delete_file_from_repo,
    ensure_git_initialized,
    untrack_engine_internal_files,
)
from ralph.git.operations import get_head_sha
from tests._support.typed_accessors import must_str

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path


pytestmark = pytest.mark.subprocess_e2e


def test_delete_file_from_repo_removes_untracked_file(tmp_git_repo: Path) -> None:
    binary = tmp_git_repo / "binary.exe"
    binary.write_text("binary content")

    assert binary.exists()

    delete_file_from_repo(tmp_git_repo, "binary.exe")

    assert not binary.exists()


@pytest.mark.timeout_seconds(5)
def test_delete_file_from_repo_unstages_staged_file(tmp_git_repo: Path) -> None:
    binary = tmp_git_repo / "staged.bin"
    binary.write_text("staged content")

    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["staged.bin"])
        repo.index.commit("add staged file")

        binary.write_text("modified content")

        delete_file_from_repo(tmp_git_repo, "staged.bin")

        assert not binary.exists()
        ls_files = repo.git.ls_files("--stage", "staged.bin")
        assert ls_files.strip() == ""
    finally:
        repo.close()


def test_delete_file_from_repo_ignores_nonexistent_path(tmp_git_repo: Path) -> None:
    delete_file_from_repo(tmp_git_repo, "nonexistent.file")
    delete_file_from_repo(tmp_git_repo, "path/does/not/exist")


def test_add_to_git_exclude_creates_file_if_missing(tmp_git_repo: Path) -> None:
    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"

    add_to_git_exclude(tmp_git_repo, ["*.pyc"])

    assert exclude_path.exists()
    content = exclude_path.read_text()
    assert "*.pyc" in content


def test_add_to_git_exclude_appends_new_patterns(tmp_git_repo: Path) -> None:
    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("existing pattern\n")

    add_to_git_exclude(tmp_git_repo, ["*.pyc", "*.log"])

    content = exclude_path.read_text()
    assert "existing pattern" in content
    assert "*.pyc" in content
    assert "*.log" in content


def test_add_to_git_exclude_deduplicates_patterns(tmp_git_repo: Path) -> None:
    exclude_path = tmp_git_repo / ".git" / "info" / "exclude"
    exclude_path.parent.mkdir(parents=True, exist_ok=True)
    exclude_path.write_text("*.pyc\n")

    add_to_git_exclude(tmp_git_repo, ["*.pyc"])

    lines = [line for line in exclude_path.read_text().splitlines() if line]
    assert lines.count("*.pyc") == 1


@pytest.mark.timeout_seconds(5)
def test_add_to_git_exclude_untracks_a_tracked_secret_without_deleting_it(
    tmp_git_repo: Path,
) -> None:
    secret = tmp_git_repo / ".env"
    secret.write_text("TOKEN=secret\n", encoding="utf-8")
    with Repo(tmp_git_repo) as repo:
        repo.index.add([".env"])
        repo.index.commit("track secret fixture")

    add_to_git_exclude(tmp_git_repo, [".env"])

    with Repo(tmp_git_repo) as repo:
        tracked = must_str(repo.git.ls_files("--cached")).splitlines()
        assert ".env" not in tracked
    assert secret.read_text(encoding="utf-8") == "TOKEN=secret\n"
    assert (
        ".env"
        in (tmp_git_repo / ".git" / "info" / "exclude").read_text(encoding="utf-8").splitlines()
    )


@pytest.mark.timeout_seconds(5)
def test_add_to_git_exclude_never_untracks_non_secret_project_files(
    tmp_git_repo: Path,
) -> None:
    settings = tmp_git_repo / "settings.ini"
    settings.write_text("[project]\n", encoding="utf-8")
    with Repo(tmp_git_repo) as repo:
        repo.index.add(["settings.ini"])
        repo.index.commit("track project settings")

    add_to_git_exclude(tmp_git_repo, ["settings.ini", "*"])

    with Repo(tmp_git_repo) as repo:
        tracked = must_str(repo.git.ls_files("--cached")).splitlines()
    assert "settings.ini" in tracked
    assert "README.md" in tracked


def test_atomic_append_text_writes_via_path_replace(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
    target = tmp_git_repo / target_subpath
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text("existing-without-newline", encoding="utf-8")

    publish_callable(tmp_git_repo, target, "*.cache")

    raw = target.read_text(encoding="utf-8")
    assert "existing-without-newline\n*.cache" in raw, (
        f"Boundary must contain a newline so the two rules are separate lines; got: {raw!r}"
    )


def test_atomic_append_text_cleans_sibling_on_exception(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    target = tmp_git_repo / "atomic_target2.txt"
    target.write_text("initial\n", encoding="utf-8")

    staging_seen: list[Path] = []
    real_unlink = type(target).unlink

    def spy_unlink(self: Path) -> None:
        staging_seen.append(self)
        real_unlink(self)

    def fake_write_bytes(self: Path, data: bytes) -> int:
        del data
        raise OSError("simulated mid-write failure")

    monkeypatch.setattr(type(target), "write_bytes", fake_write_bytes)
    monkeypatch.setattr(type(target), "unlink", spy_unlink)

    with pytest.raises(OSError, match="simulated mid-write failure"):
        ops._atomic_append_text(target, "extra\n", encoding="utf-8")

    assert staging_seen, "Helper must call staging.unlink() after a failed write_bytes"
    assert target.read_text(encoding="utf-8") == "initial\n", (
        "Target file must NOT be modified when the publish step fails"
    )


def test_add_to_git_exclude_routes_through_atomic_helper(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        f"Atomic helper must be called with the exclude file path, got: {written_path}"
    )


def test_append_to_gitignore_routes_through_atomic_helper(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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
        f"Atomic helper must be called with the .gitignore path, got: {written_path}"
    )


def test_delete_file_from_repo_rejects_symlink_pointing_to_sibling_inside_repo(
    tmp_git_repo: Path,
) -> None:
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
    outside = tmp_path / "outside.txt"
    outside.write_text("outside the repo\n")
    escape_link = tmp_git_repo / "escape_link"
    escape_link.symlink_to(outside)

    with pytest.raises(ValueError, match="symlink"):
        delete_file_from_repo(tmp_git_repo, "escape_link")

    assert escape_link.is_symlink(), "The escape symlink itself must be preserved"
    assert outside.exists(), "The outside file must NOT be deleted"


def test_delete_file_from_repo_rejects_broken_symlink_inside_repo(tmp_git_repo: Path) -> None:
    broken_link = tmp_git_repo / "broken_link"
    broken_link.symlink_to(tmp_git_repo / "nonexistent_target")

    with pytest.raises(ValueError, match="symlink"):
        delete_file_from_repo(tmp_git_repo, "broken_link")

    assert broken_link.is_symlink(), "The broken symlink itself must be preserved"


def test_delete_file_from_repo_propagates_oserror_on_unlink_failure(
    tmp_git_repo: Path,
) -> None:
    undeletable = tmp_git_repo / "undeletable_dir"
    undeletable.mkdir()

    assert undeletable.is_dir()
    with pytest.raises(OSError):
        delete_file_from_repo(tmp_git_repo, "undeletable_dir")

    assert undeletable.is_dir(), "The directory must NOT be deleted after the failed call"


def test_ensure_git_initialized_inits_non_repo_directory(tmp_path: Path) -> None:
    non_repo = tmp_path / "non_repo"
    non_repo.mkdir()

    assert not (non_repo / ".git").exists()

    ensure_git_initialized(non_repo)

    assert (non_repo / ".git").exists()
    with Repo(non_repo) as repo:
        assert repo.active_branch.name in ("main", "master", "HEAD")


def test_ensure_git_initialized_is_noop_for_existing_repo(tmp_git_repo: Path) -> None:
    ensure_git_initialized(tmp_git_repo)

    with Repo(tmp_git_repo) as repo:
        assert repo.active_branch.name in ("main", "master", "HEAD")
        assert repo.head.commit.hexsha


@pytest.mark.timeout_seconds(5)
def test_delete_file_from_repo_handles_untracked_existing_file(tmp_git_repo: Path) -> None:
    binary = tmp_git_repo / "untracked.exe"
    binary.write_text("binary content")

    assert binary.exists()
    assert not binary.is_symlink()

    delete_file_from_repo(tmp_git_repo, "untracked.exe")

    assert not binary.exists(), "Untracked existing file MUST be removed by delete_file_from_repo"


@pytest.mark.timeout_seconds(5)
def test_delete_file_from_repo_handles_dot_slash_prefixed_path(tmp_git_repo: Path) -> None:
    binary = tmp_git_repo / "binary.exe"
    binary.write_text("binary content")

    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["binary.exe"])
        repo.index.commit("track binary")
    finally:
        repo.close()

    delete_file_from_repo(tmp_git_repo, "./binary.exe")

    assert not binary.exists(), "File MUST be removed when the path uses a ``./`` prefix"


@pytest.mark.timeout_seconds(5)
def test_delete_file_from_repo_rejects_symlinked_parent_dir(
    tmp_git_repo: Path, tmp_path: Path
) -> None:
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

    assert (outside / "foo.txt").exists(), "The outside file must NOT be deleted as a side effect"


@pytest.mark.timeout_seconds(5)
def test_delete_file_from_repo_propagates_index_lock_error(
    tmp_git_repo: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    binary = tmp_git_repo / "binary.exe"
    binary.write_text("binary content")

    repo = Repo(tmp_git_repo)
    try:
        repo.index.add(["binary.exe"])
        repo.index.commit("track binary")
    finally:
        repo.close()

    monkeypatch.setattr("ralph.git._lock_recovery._LIVE_HOLDER_PATIENCE_S", 0.0)
    (tmp_git_repo / ".git" / "index.lock").write_text("locked", encoding="utf-8")
    assert (tmp_git_repo / ".git" / "index.lock").exists()

    try:
        with pytest.raises(GitCommandError) as excinfo:
            delete_file_from_repo(tmp_git_repo, "binary.exe")
        assert "index.lock" in str(excinfo.value) or "File exists" in str(excinfo.value), (
            f"Stderr must surface the index-lock conflict, got: {excinfo.value!s}"
        )
    finally:
        with suppress(FileNotFoundError):
            (tmp_git_repo / ".git" / "index.lock").unlink()


@pytest.mark.timeout_seconds(5)
def test_delete_file_from_repo_accepts_str_path_argument(tmp_git_repo: Path) -> None:
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

    assert not binary.exists(), "File MUST be removed when repo_root is passed as str"


@pytest.mark.timeout_seconds(5)
def test_ensure_git_initialized_accepts_str_path_argument(tmp_path: Path) -> None:
    non_repo = tmp_path / "non_repo_str"
    non_repo.mkdir()
    assert not (non_repo / ".git").exists()

    ensure_git_initialized(str(non_repo))

    assert (non_repo / ".git").exists()
    with Repo(non_repo) as repo:
        assert repo.active_branch.name in ("main", "master", "HEAD")


@pytest.mark.timeout_seconds(5)
def test_ensure_git_initialized_does_not_clobber_existing_repo(tmp_git_repo: Path) -> None:
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

    ensure_git_initialized(worktree)

    with Repo(separate_dir) as repo:
        assert repo.active_branch.name in ("main", "master", "HEAD")


@pytest.mark.timeout_seconds(5)
def test_ensure_git_initialized_raises_on_corrupt_git_dir(tmp_path: Path) -> None:
    corrupt = tmp_path / "corrupt_repo"
    corrupt.mkdir()
    (corrupt / ".git").write_text("not a git directory, not a gitfile\n", encoding="utf-8")
    assert (corrupt / ".git").is_file()

    with pytest.raises(GitCommandError) as excinfo:
        ensure_git_initialized(corrupt)
    assert "invalid gitfile format" in str(excinfo.value), (
        f"Stderr must surface the invalid-gitfile-format error, got: {excinfo.value!s}"
    )
    assert (corrupt / ".git").is_file(), (
        "ensure_git_initialized must NOT silently re-init over a corrupt .git"
    )
    assert (corrupt / ".git").read_text(encoding="utf-8") == (
        "not a git directory, not a gitfile\n"
    ), "Corrupt .git file content must NOT have been modified by a silent re-init"


def _track_and_commit(repo_root: Path, rel_path: str) -> None:
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

    assert log_path.exists(), "Working-tree file must remain after ``git rm --cached``"
    assert mcp_log_path.exists(), "Working-tree file must remain after ``git rm --cached``"
    assert checkpoint_path.exists(), "Working-tree file must remain after ``git rm --cached``"


@pytest.mark.timeout_seconds(5)
def test_untrack_engine_internal_files_preserves_non_engine_files(
    tmp_git_repo: Path,
) -> None:
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
        f"Helper must not untrack anything when predicate rejects every path, got: {untracked!r}"
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

    captured: list[tuple[str, str]] = []
    sink_id = logger.add(
        lambda msg: captured.append((msg.record["level"].name, str(msg))),
        level="INFO",
    )
    try:
        untracked = untrack_engine_internal_files(tmp_git_repo, _engine_path)
    finally:
        logger.remove(sink_id)

    assert untracked == [], f"Symlink must be skipped by the helper, got: {untracked!r}"

    refusal_messages = [
        (level, msg)
        for level, msg in captured
        if "Refusing to git rm --cached symlink under tracked engine-internal path" in msg
    ]
    assert len(refusal_messages) == 1, (
        f"Expected exactly one refusal log message; got: {refusal_messages!r}"
    )
    assert refusal_messages[0][0] == "INFO", (
        f"Symlink refusal message must be logged at INFO level; got: {refusal_messages[0]!r}"
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
    shutil.rmtree(tmp_git_repo)
    tmp_git_repo.mkdir()
    Repo.init(tmp_git_repo)

    untracked = untrack_engine_internal_files(tmp_git_repo, lambda _p: True)

    assert untracked == [], f"Helper must return [] for an empty index, got: {untracked!r}"


@pytest.mark.timeout_seconds(5)
def test_untrack_engine_internal_files_handles_non_git_directory(tmp_path: Path) -> None:
    non_repo = tmp_path / "non_repo"
    non_repo.mkdir()
    assert not (non_repo / ".git").exists(), "Setup invariant: non_repo must not be a git repo"

    untracked = untrack_engine_internal_files(non_repo, lambda _p: True)

    assert untracked == [], f"Helper must return [] for a non-git directory, got: {untracked!r}"


@pytest.mark.timeout_seconds(5)
def test_untrack_engine_internal_files_skills_silent(
    tmp_git_repo: Path,
) -> None:
    canonical_skill = tmp_git_repo / ".opencode" / "skills" / "brainstorming"
    canonical_skill.mkdir(parents=True, exist_ok=True)
    canonical_skill.joinpath("SKILL.md").write_text("# brainstorming skill\n", encoding="utf-8")
    sibling_skill = tmp_git_repo / ".agents" / "skills" / "brainstorming"
    sibling_skill.parent.mkdir(parents=True, exist_ok=True)
    sibling_skill.symlink_to(canonical_skill, target_is_directory=True)

    _track_and_commit(tmp_git_repo, ".opencode/skills/brainstorming/SKILL.md")
    _track_and_commit(tmp_git_repo, ".agents/skills/brainstorming")

    captured_warnings: list[str] = []
    sink_id = logger.add(captured_warnings.append, level="WARNING", format="{message}")
    try:

        def _engine_path(p: str) -> bool:
            return p == ".agents/skills/brainstorming"

        untracked = untrack_engine_internal_files(tmp_git_repo, _engine_path)
    finally:
        logger.remove(sink_id)

    assert untracked == [], f"Skill symlinks must NOT be untracked; got: {untracked!r}"
    assert captured_warnings == [], (
        f"No WARNING should be emitted for tracked skill-root paths; got: {captured_warnings!r}"
    )

    repo = Repo(tmp_git_repo)
    try:
        cached = set(repo.git.ls_files("--cached").splitlines())
        assert ".agents/skills/brainstorming" in cached, (
            "Tracked skill-root symlink MUST remain in git ls-files --cached after the early-skip"
        )
    finally:
        repo.close()


@pytest.mark.timeout_seconds(10)
def test_untrack_engine_internal_files_does_not_warn_on_skill_symlinks(
    tmp_git_repo: Path,
) -> None:
    canonical_a = tmp_git_repo / ".opencode" / "skills" / "coding-standards"
    canonical_a.mkdir(parents=True, exist_ok=True)
    canonical_a.joinpath("SKILL.md").write_text("# coding-standards skill\n", encoding="utf-8")
    sibling_a = tmp_git_repo / ".agents" / "skills" / "coding-standards"
    sibling_a.parent.mkdir(parents=True, exist_ok=True)
    sibling_a.symlink_to(canonical_a, target_is_directory=True)

    canonical_b = tmp_git_repo / ".opencode" / "skills" / "brainstorming"
    canonical_b.mkdir(parents=True, exist_ok=True)
    canonical_b.joinpath("SKILL.md").write_text("# brainstorming skill\n", encoding="utf-8")

    _track_and_commit(tmp_git_repo, ".opencode/skills/coding-standards/SKILL.md")
    _track_and_commit(tmp_git_repo, ".agents/skills/coding-standards")
    _track_and_commit(tmp_git_repo, ".opencode/skills/brainstorming/SKILL.md")

    prompt_path = tmp_git_repo / ".agent" / "PROMPT.md"
    prompt_path.parent.mkdir(parents=True, exist_ok=True)
    prompt_path.write_text("# prompt\n", encoding="utf-8")
    _track_and_commit(tmp_git_repo, ".agent/PROMPT.md")

    captured: list[str] = []
    sink_id = logger.add(captured.append, level="DEBUG", format="{message}")
    try:

        def _engine_path(p: str) -> bool:
            return p == ".agent/PROMPT.md"

        untracked = untrack_engine_internal_files(tmp_git_repo, _engine_path)
    finally:
        logger.remove(sink_id)

    offending = [
        msg
        for msg in captured
        if "Refusing to git rm --cached symlink under tracked engine-internal path" in msg
    ]
    assert offending == [], (
        f"Helper must NOT emit the WARNING line for tracked skill symlinks; got: {offending!r}"
    )

    early_skip_messages = [msg for msg in captured if "Skipping tracked skill-root path" in msg]
    assert early_skip_messages, (
        f"Early-skip block MUST emit at least one DEBUG message containing "
        f"'Skipping tracked skill-root path'; captured: {captured!r}"
    )

    assert untracked == [".agent/PROMPT.md"], (
        f"Engine-internal ``.agent/PROMPT.md`` MUST be untracked; got: {untracked!r}"
    )

    repo = Repo(tmp_git_repo)
    try:
        cached_paths = set(repo.git.ls_files("--cached").splitlines())
        assert ".agents/skills/coding-standards" in cached_paths, (
            f"Tracked FIVE-root symlink MUST remain in git ls-files --cached; got: {cached_paths!r}"
        )
        assert ".opencode/skills/coding-standards/SKILL.md" in cached_paths, (
            "Tracked FIVE-root canonical file MUST remain in git ls-files --cached; "
            f"got: {cached_paths!r}"
        )
        assert ".opencode/skills/brainstorming/SKILL.md" in cached_paths, (
            "Tracked FIVE-root canonical file MUST remain in git ls-files --cached; "
            f"got: {cached_paths!r}"
        )
        assert ".agent/PROMPT.md" not in cached_paths, (
            "Tracked engine-internal file MUST be removed from git ls-files --cached"
        )
    finally:
        repo.close()

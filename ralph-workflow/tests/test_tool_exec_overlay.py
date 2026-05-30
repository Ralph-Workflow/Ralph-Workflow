from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.tools import exec_overlay

if TYPE_CHECKING:
    import pytest


def test_process_identity_matches_matching_start_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = os.getpid()
    monkeypatch.setattr(exec_overlay, "_current_process_identity", lambda: (pid, 123.0))

    assert exec_overlay._process_identity_matches(pid, 123.0) is True


def test_process_identity_matches_rejects_reused_pid_with_different_start_time(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    pid = os.getpid()
    monkeypatch.setattr(exec_overlay, "_current_process_identity", lambda: (pid, 456.0))

    assert exec_overlay._process_identity_matches(pid, 123.0) is False


def test_sync_dir_copies_new_files(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "a.txt").write_text("hello")
    (src / "b.txt").write_text("world")

    exec_overlay._sync_dir(src, dst, frozenset(), frozenset())

    assert (dst / "a.txt").read_text() == "hello"
    assert (dst / "b.txt").read_text() == "world"


def test_sync_dir_removes_stale_files_from_dst(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()
    (src / "keep.txt").write_text("keep")
    (dst / "stale.txt").write_text("remove-me")
    (dst / "keep.txt").write_text("old")

    exec_overlay._sync_dir(src, dst, frozenset(), frozenset())

    assert (dst / "keep.txt").read_text() == "keep"
    assert not (dst / "stale.txt").exists()


def test_sync_dir_excludes_generated_names(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / "a.txt").write_text("a")
    pycache = src / "__pycache__"
    pycache.mkdir()
    (pycache / "c.pyc").write_text("ignored")

    exec_overlay._sync_dir(src, dst, frozenset({"__pycache__"}), frozenset())

    assert (dst / "a.txt").exists()
    assert not (dst / "__pycache__").exists()


def test_ignored_workspace_relative_paths_is_cached(tmp_path: Path) -> None:
    workspace = tmp_path / "repo"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    first = exec_overlay._ignored_workspace_relative_paths(workspace)
    second = exec_overlay._ignored_workspace_relative_paths(workspace)

    assert first == second
    assert first is second


def test_sync_dir_copies_nested_directories(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    sub = src / "sub"
    sub.mkdir()
    (sub / "nested.txt").write_text("deep")

    exec_overlay._sync_dir(src, dst, frozenset(), frozenset())

    assert (dst / "sub" / "nested.txt").read_text() == "deep"


def test_mirror_workspace_rsync_excludes_dot_git(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    captured_cmd: list[str] = []

    class FakeResult:
        returncode = 0

    def fake_run(cmd: list[str], **kwargs: object) -> FakeResult:
        if cmd[0].endswith("rsync"):
            captured_cmd.extend(cmd)
        return FakeResult()

    monkeypatch.setattr(
        exec_overlay.shutil, "which", lambda cmd: "/usr/bin/rsync" if cmd == "rsync" else None
    )
    monkeypatch.setattr(exec_overlay, "_RSYNC_BIN", "/usr/bin/rsync", raising=False)
    monkeypatch.setattr(exec_overlay.subprocess, "run", fake_run)

    workspace = tmp_path / "workspace"
    workspace.mkdir()
    (workspace / ".git").mkdir()

    overlay = tmp_path / "overlay"
    exec_overlay._mirror_workspace(workspace, overlay)

    assert "--exclude=/.git" in captured_cmd


def test_mirror_workspace_rsync_does_not_call_which_per_invocation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    which_call_count = 0

    def counting_which(cmd: str) -> str | None:
        nonlocal which_call_count
        which_call_count += 1
        return "/usr/bin/rsync"

    class FakeResult:
        returncode = 0

    monkeypatch.setattr(exec_overlay, "_RSYNC_BIN", "/usr/bin/rsync", raising=False)
    monkeypatch.setattr(exec_overlay.shutil, "which", counting_which)
    monkeypatch.setattr(exec_overlay.subprocess, "run", lambda *a, **kw: FakeResult())

    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    dst.mkdir()

    exec_overlay._mirror_workspace_rsync(src, dst, [])
    exec_overlay._mirror_workspace_rsync(src, dst, [])

    assert which_call_count == 0


def test_git_state_fingerprint_includes_head_content(tmp_path: Path) -> None:
    gitdir = tmp_path / "gitdir"
    gitdir.mkdir()
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")

    result = exec_overlay._git_state_fingerprint(gitdir)

    assert "ref: refs/heads/main" in result


def test_git_state_fingerprint_changes_when_head_changes(tmp_path: Path) -> None:
    gitdir = tmp_path / "gitdir"
    gitdir.mkdir()
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    fp1 = exec_overlay._git_state_fingerprint(gitdir)

    (gitdir / "HEAD").write_text("ref: refs/heads/other\n", encoding="utf-8")
    fp2 = exec_overlay._git_state_fingerprint(gitdir)

    assert fp1 != fp2


def test_git_state_fingerprint_changes_when_index_mtime_changes(tmp_path: Path) -> None:
    gitdir = tmp_path / "gitdir"
    gitdir.mkdir()
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (gitdir / "index").write_bytes(b"initial")
    fp1 = exec_overlay._git_state_fingerprint(gitdir)

    (gitdir / "index").write_bytes(b"updated-bytes")
    fp2 = exec_overlay._git_state_fingerprint(gitdir)

    assert fp1 != fp2


def test_git_state_fingerprint_changes_when_branch_commit_changes(tmp_path: Path) -> None:
    gitdir = tmp_path / "gitdir"
    gitdir.mkdir()
    (gitdir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (gitdir / "refs" / "heads").mkdir(parents=True)
    (gitdir / "refs" / "heads" / "main").write_text("abc\n", encoding="utf-8")
    fp1 = exec_overlay._git_state_fingerprint(gitdir)

    (gitdir / "refs" / "heads" / "main").write_text("def\n", encoding="utf-8")
    fp2 = exec_overlay._git_state_fingerprint(gitdir)

    assert fp1 != fp2


def test_git_state_fingerprint_tolerates_missing_files(tmp_path: Path) -> None:
    gitdir = tmp_path / "gitdir"
    gitdir.mkdir()

    result = exec_overlay._git_state_fingerprint(gitdir)

    assert isinstance(result, str)


def test_read_git_fingerprint_returns_none_when_missing(tmp_path: Path) -> None:
    result = exec_overlay._read_git_fingerprint(tmp_path / "nonexistent")

    assert result is None


def test_write_and_read_git_fingerprint_round_trips(tmp_path: Path) -> None:
    exec_overlay._write_git_fingerprint(tmp_path, "sentinel-value")
    result = exec_overlay._read_git_fingerprint(tmp_path)

    assert result == "sentinel-value"


def _make_workspace_with_git(tmp_path: Path) -> Path:
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    git_dir = workspace / ".git"
    git_dir.mkdir()
    (git_dir / "objects").mkdir()
    (git_dir / "refs" / "heads").mkdir(parents=True)
    (git_dir / "HEAD").write_text("ref: refs/heads/main\n", encoding="utf-8")
    (git_dir / "refs" / "heads" / "main").write_text("abcdef\n", encoding="utf-8")
    return workspace


def test_ensure_git_isolation_preserves_private_gitdir_inode_on_repeat_call_with_same_git_state(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace_with_git(tmp_path)
    sandbox_root = tmp_path / "sandbox"
    sandbox_root.mkdir()
    worktree = sandbox_root / "ws"
    worktree.mkdir()

    exec_overlay._ensure_git_isolation(workspace, worktree, sandbox_root)
    inode_before = (sandbox_root / "private-gitdir").stat().st_ino

    exec_overlay._ensure_git_isolation(workspace, worktree, sandbox_root)
    inode_after = (sandbox_root / "private-gitdir").stat().st_ino

    assert inode_after == inode_before


def test_ensure_git_isolation_recreates_private_gitdir_when_head_changes(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace_with_git(tmp_path)
    sandbox_root = tmp_path / "sandbox"
    sandbox_root.mkdir()
    worktree = sandbox_root / "ws"
    worktree.mkdir()

    exec_overlay._ensure_git_isolation(workspace, worktree, sandbox_root)
    inode_before = (sandbox_root / "private-gitdir").stat().st_ino

    (workspace / ".git" / "HEAD").write_text("ref: refs/heads/other\n", encoding="utf-8")
    exec_overlay._ensure_git_isolation(workspace, worktree, sandbox_root)
    inode_after = (sandbox_root / "private-gitdir").stat().st_ino

    assert inode_after != inode_before


def test_ensure_git_isolation_runs_full_setup_when_fingerprint_absent(
    tmp_path: Path,
) -> None:
    workspace = _make_workspace_with_git(tmp_path)
    sandbox_root = tmp_path / "sandbox"
    sandbox_root.mkdir()
    worktree = sandbox_root / "ws"
    worktree.mkdir()

    private_gitdir = sandbox_root / "private-gitdir"
    private_gitdir.mkdir()
    (worktree / ".git").write_text(f"gitdir: {private_gitdir}\n", encoding="utf-8")
    inode_before = private_gitdir.stat().st_ino

    exec_overlay._ensure_git_isolation(workspace, worktree, sandbox_root)
    inode_after = (sandbox_root / "private-gitdir").stat().st_ino

    assert inode_after != inode_before


def test_sync_dir_copies_symlink_in_source(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    target = src / "real.txt"
    target.write_text("content")
    link = src / "link.txt"
    link.symlink_to(target)

    exec_overlay._sync_dir(src, dst, frozenset(), frozenset())

    assert (dst / "real.txt").exists()
    assert (dst / "link.txt").exists()


def test_sync_dir_skips_entry_matching_ignored_path(tmp_path: Path) -> None:
    src = tmp_path / "src"
    dst = tmp_path / "dst"
    src.mkdir()
    (src / ".git").mkdir()
    (src / ".git" / "config").write_text("gitconfig")
    (src / "code.py").write_text("code")

    exec_overlay._sync_dir(
        src, dst, frozenset(), frozenset({Path(".git")})
    )

    assert (dst / "code.py").exists()
    assert not (dst / ".git").exists()


def test_mirror_workspace_incremental_hard_links_unchanged_files(tmp_path: Path) -> None:
    workspace = tmp_path / "ws"
    workspace.mkdir()
    (workspace / "a.txt").write_text("unchanged")
    (workspace / "b.txt").write_text("old")

    previous = tmp_path / "prev"
    previous.mkdir()
    (previous / "a.txt").write_text("unchanged")
    (previous / "b.txt").write_text("old")

    (workspace / "b.txt").write_text("new!")
    (workspace / "c.txt").write_text("added")

    overlay = tmp_path / "overlay"
    exec_overlay._mirror_workspace(workspace, overlay, link_dest=previous)

    assert (overlay / "a.txt").read_text() == "unchanged"
    assert (overlay / "b.txt").read_text() == "new!"
    assert (overlay / "c.txt").read_text() == "added"

    a_ino = (overlay / "a.txt").stat().st_ino
    prev_a_ino = (previous / "a.txt").stat().st_ino
    b_ino = (overlay / "b.txt").stat().st_ino
    prev_b_ino = (previous / "b.txt").stat().st_ino

    assert a_ino == prev_a_ino
    assert b_ino != prev_b_ino

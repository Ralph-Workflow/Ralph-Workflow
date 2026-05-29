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

    a_ino = os.stat(overlay / "a.txt").st_ino
    prev_a_ino = os.stat(previous / "a.txt").st_ino
    b_ino = os.stat(overlay / "b.txt").st_ino
    prev_b_ino = os.stat(previous / "b.txt").st_ino

    assert a_ino == prev_a_ino
    assert b_ino != prev_b_ino

"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

import pytest

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceInit:
    def test_accepts_path_object(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(Path(tmpdir))
            assert ws.root == Path(tmpdir).resolve()

    def test_accepts_string_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            assert ws.root == Path(tmpdir).resolve()


class TestFsWorkspaceRead:
    def test_reads_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "test.txt").write_text("hello world", encoding="utf-8")

            content = ws.read("test.txt")
            assert content == "hello world"

    def test_read_nonexistent_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            with pytest.raises(FileNotFoundError):
                ws.read("nonexistent.txt")

    def test_read_with_subdirectory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            subdir = Path(tmpdir) / "sub" / "dir"
            subdir.mkdir(parents=True)
            (subdir / "file.txt").write_text("nested", encoding="utf-8")

            content = ws.read("sub/dir/file.txt")
            assert content == "nested"


class TestFsWorkspaceWrite:
    def test_writes_file_content(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.write("output.txt", "test content")

            assert (Path(tmpdir) / "output.txt").read_text(encoding="utf-8") == "test content"

    def test_writes_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.write("a/b/c/deep.txt", "content")

            assert (Path(tmpdir) / "a" / "b" / "c" / "deep.txt").read_text(
                encoding="utf-8"
            ) == "content"

    def test_writes_overwrites_existing(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "existing.txt").write_text("original", encoding="utf-8")

            ws.write("existing.txt", "updated")

            assert (Path(tmpdir) / "existing.txt").read_text(encoding="utf-8") == "updated"

    def test_write_rejects_parent_traversal_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            with pytest.raises(ValueError, match="outside workspace"):
                ws.write("../escape.txt", "blocked")

    def test_write_rejects_absolute_path_outside_workspace(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            with pytest.raises(ValueError, match="outside workspace"):
                ws.write("/tmp/escape.txt", "blocked")


class TestFsWorkspaceAppend:
    def test_appends_to_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "append.txt").write_text("start", encoding="utf-8")

            ws.append("append.txt", "_end")

            assert (Path(tmpdir) / "append.txt").read_text(encoding="utf-8") == "start_end"

    def test_append_creates_new_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.append("new.txt", "first")

            assert (Path(tmpdir) / "new.txt").read_text(encoding="utf-8") == "first"

    def test_append_creates_parent_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.append("x/y/file.txt", "content")

            assert (Path(tmpdir) / "x" / "y" / "file.txt").read_text(encoding="utf-8") == "content"


class TestFsWorkspaceExists:
    def test_returns_true_for_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "exists.txt").write_text("hi", encoding="utf-8")

            assert ws.exists("exists.txt") is True

    def test_returns_false_for_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            assert ws.exists("missing.txt") is False


class TestFsWorkspaceRemove:
    def test_removes_existing_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "todelete.txt").write_text("remove me", encoding="utf-8")

            ws.remove("todelete.txt")

            assert (Path(tmpdir) / "todelete.txt").exists() is False

    def test_remove_nonexistent_does_not_raise(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.remove("nonexistent.txt")


class TestFsWorkspaceListDir:
    def test_lists_directory_contents(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "a.txt").write_text("", encoding="utf-8")
            (Path(tmpdir) / "b.txt").write_text("", encoding="utf-8")

            entries = ws.list_dir(".")

            assert "a.txt" in entries
            assert "b.txt" in entries

    def test_list_dir_empty_for_nonexistent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            entries = ws.list_dir("nonexistent")

            assert entries == []


class TestFsWorkspaceIsDir:
    def test_returns_true_for_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "subdir").mkdir()

            assert ws.is_dir("subdir") is True

    def test_returns_false_for_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "afile.txt").write_text("", encoding="utf-8")

            assert ws.is_dir("afile.txt") is False


class TestFsWorkspaceIsFile:
    def test_returns_true_for_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "file.txt").write_text("", encoding="utf-8")

            assert ws.is_file("file.txt") is True

    def test_returns_false_for_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "subdir").mkdir()

            assert ws.is_file("subdir") is False


class TestFsWorkspaceAbsolutePath:
    def test_returns_absolute_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            abs_path = ws.absolute_path("some/file.txt")

            assert abs_path.startswith(str(Path(tmpdir).resolve()))
            assert "some/file.txt" in abs_path

    def test_absolute_path_rejects_parent_traversal(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            with pytest.raises(ValueError, match="outside workspace"):
                ws.absolute_path("../escape.txt")


class TestFsWorkspaceRoot:
    def test_returns_root_path(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            assert ws.root == Path(tmpdir).resolve()

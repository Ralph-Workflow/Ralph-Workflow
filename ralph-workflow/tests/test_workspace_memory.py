"""Tests for ralph/workspace/memory.py — In-memory workspace implementation."""

from __future__ import annotations

import pytest

from ralph.workspace.memory import MemoryWorkspace


class TestMemoryWorkspaceInit:
    def test_default_initialization(self) -> None:
        ws = MemoryWorkspace()
        assert ws._storage == {}
        assert ws._dirs == {""}

    def test_custom_root(self) -> None:
        ws = MemoryWorkspace("/custom/root")
        assert ws._root.name == "root"


class TestMemoryWorkspaceNormalize:
    def test_empty_string(self) -> None:
        ws = MemoryWorkspace()
        assert ws._normalize("") == ""

    def test_dot_becomes_empty(self) -> None:
        ws = MemoryWorkspace()
        assert ws._normalize(".") == ""

    def test_path_normalization(self) -> None:
        ws = MemoryWorkspace()
        assert ws._normalize("a/b/c") == "a/b/c"


class TestMemoryWorkspaceRead:
    def test_read_existing_file(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "content")

        assert ws.read("file.txt") == "content"

    def test_read_nonexistent_raises(self) -> None:
        ws = MemoryWorkspace()

        with pytest.raises(FileNotFoundError) as exc_info:
            ws.read("missing.txt")
        assert "not found" in str(exc_info.value)


class TestMemoryWorkspaceWrite:
    def test_write_creates_file(self) -> None:
        ws = MemoryWorkspace()
        ws.write("new.txt", "hello")

        assert ws.read("new.txt") == "hello"

    def test_write_overwrites(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "v1")
        ws.write("file.txt", "v2")

        assert ws.read("file.txt") == "v2"

    def test_write_creates_parent_dirs(self) -> None:
        ws = MemoryWorkspace()
        ws.write("a/b/c/deep.txt", "content")

        assert ws.read("a/b/c/deep.txt") == "content"
        assert ws.is_dir("a/b/c")


class TestMemoryWorkspaceAppend:
    def test_append_to_existing(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "start")

        ws.append("file.txt", "_end")

        assert ws.read("file.txt") == "start_end"

    def test_append_creates_new_file(self) -> None:
        ws = MemoryWorkspace()
        ws.append("new.txt", "first")

        assert ws.read("new.txt") == "first"


class TestMemoryWorkspaceExists:
    def test_exists_for_written_file(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "content")

        assert ws.exists("file.txt") is True

    def test_not_exists_for_missing(self) -> None:
        ws = MemoryWorkspace()

        assert ws.exists("missing.txt") is False


class TestMemoryWorkspaceRemove:
    def test_remove_existing_file(self) -> None:
        ws = MemoryWorkspace()
        ws.write("todelete.txt", "content")

        ws.remove("todelete.txt")

        assert ws.exists("todelete.txt") is False

    def test_remove_nonexistent_no_error(self) -> None:
        ws = MemoryWorkspace()
        ws.remove("nonexistent.txt")


class TestMemoryWorkspaceListDir:
    def test_list_empty_directory(self) -> None:
        ws = MemoryWorkspace()

        entries = ws.list_dir("")
        assert entries == []

    def test_list_files_in_root(self) -> None:
        ws = MemoryWorkspace()
        ws.write("a.txt", "")
        ws.write("b.txt", "")

        entries = ws.list_dir("")
        assert "a.txt" in entries
        assert "b.txt" in entries

    def test_list_nested_files(self) -> None:
        ws = MemoryWorkspace()
        ws.write("dir/file.txt", "")

        entries = ws.list_dir("")
        assert "dir" in entries

    def test_list_empty_subdirectory(self) -> None:
        ws = MemoryWorkspace()
        ws.create_dir("empty_dir")

        entries = ws.list_dir("")
        assert "empty_dir" in entries


class TestMemoryWorkspaceIsDir:
    def test_is_dir_for_created_directory(self) -> None:
        ws = MemoryWorkspace()
        ws.create_dir("mydir")

        assert ws.is_dir("mydir") is True

    def test_is_dir_false_for_file(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "")

        assert ws.is_dir("file.txt") is False

    def test_is_dir_false_for_nonexistent(self) -> None:
        ws = MemoryWorkspace()

        assert ws.is_dir("missing") is False


class TestMemoryWorkspaceIsFile:
    def test_is_file_true_for_file(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "")

        assert ws.is_file("file.txt") is True

    def test_is_file_false_for_directory(self) -> None:
        ws = MemoryWorkspace()
        ws.create_dir("mydir")

        assert ws.is_file("mydir") is False

    def test_is_file_false_for_nonexistent(self) -> None:
        ws = MemoryWorkspace()

        assert ws.is_file("missing.txt") is False


class TestMemoryWorkspaceClear:
    def test_clear_removes_all_files(self) -> None:
        ws = MemoryWorkspace()
        ws.write("a.txt", "")
        ws.write("b.txt", "")

        ws.clear()

        assert ws.exists("a.txt") is False
        assert ws.exists("b.txt") is False


class TestMemoryWorkspaceCreateDir:
    def test_create_dir(self) -> None:
        ws = MemoryWorkspace()
        ws.create_dir("newdir")

        assert ws.is_dir("newdir") is True

    def test_create_nested_dir(self) -> None:
        ws = MemoryWorkspace()
        ws.create_dir("a/b/c")

        assert ws.is_dir("a/b/c") is True


class TestMemoryWorkspaceAbsolutePath:
    def test_returns_absolute_path(self) -> None:
        ws = MemoryWorkspace("/workspace")

        abs_path = ws.absolute_path("file.txt")

        assert "file.txt" in abs_path
        assert ws._root.name in abs_path


class TestMemoryWorkspaceReadBytes:
    def test_full_read_returns_all_content(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "Hello, World!")

        text, meta = ws.read_bytes("file.txt")

        assert text == "Hello, World!"
        assert meta["total_bytes"] == len(b"Hello, World!")
        assert meta["returned_bytes"] == meta["total_bytes"]
        assert meta["truncated"] is False

    def test_offset_skips_leading_bytes(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "Hello, World!")

        text, meta = ws.read_bytes("file.txt", offset=7)

        assert text == "World!"
        assert meta["truncated"] is False

    def test_limit_truncates_content(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "Hello, World!")

        text, meta = ws.read_bytes("file.txt", limit=5)

        assert text == "Hello"
        assert meta["returned_bytes"] == 5  # noqa: PLR2004
        assert meta["truncated"] is True

    def test_offset_and_limit_together(self) -> None:
        ws = MemoryWorkspace()
        ws.write("file.txt", "Hello, World!")

        text, meta = ws.read_bytes("file.txt", offset=7, limit=5)

        assert text == "World"
        assert meta["truncated"] is True

    def test_missing_file_raises_file_not_found(self) -> None:
        ws = MemoryWorkspace()

        with pytest.raises(FileNotFoundError):
            ws.read_bytes("nonexistent.txt")

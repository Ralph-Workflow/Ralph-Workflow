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


class TestFsWorkspaceReadLines:
    def test_read_lines_returns_all_lines(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "lines.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")

            content, meta = ws.read_lines("lines.txt")
            assert content == "line1\nline2\nline3\n"
            assert meta["total_lines"] == 3  # noqa: PLR2004
            assert meta["returned_lines"] == 3  # noqa: PLR2004
            assert meta["truncated"] is False

    def test_read_lines_head_returns_first_n(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "lines.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")

            content, meta = ws.read_lines("lines.txt", head=2)
            assert content == "line1\nline2\n"
            assert meta["returned_lines"] == 2  # noqa: PLR2004
            assert meta["truncated"] is True

    def test_read_lines_tail_returns_last_n(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "lines.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")

            content, meta = ws.read_lines("lines.txt", tail=2)
            assert content == "line2\nline3\n"
            assert meta["returned_lines"] == 2  # noqa: PLR2004
            assert meta["truncated"] is True

    def test_read_lines_start_end_returns_range(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "lines.txt").write_text("line1\nline2\nline3\n", encoding="utf-8")

            content, meta = ws.read_lines("lines.txt", start=2, end=3)
            assert content == "line2\nline3\n"
            assert meta["total_lines"] == 3  # noqa: PLR2004
            assert meta["returned_lines"] == 2  # noqa: PLR2004

    def test_read_lines_conflicting_params_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "lines.txt").write_text("line1\nline2\n", encoding="utf-8")

            with pytest.raises(ValueError, match="Only one of"):
                ws.read_lines("lines.txt", head=2, tail=2)

    def test_read_lines_nonexistent_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            with pytest.raises(FileNotFoundError):
                ws.read_lines("missing.txt")


class TestFsWorkspaceStat:
    def test_stat_file_returns_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            file_path = Path(tmpdir) / "file.txt"
            file_path.write_text("hello", encoding="utf-8")

            result = ws.stat("file.txt")
            assert result["type"] == "file"
            assert result["size_bytes"] == 5  # noqa: PLR2004
            assert "created_unix" in result
            assert "modified_unix" in result
            assert "mode" in result

    def test_stat_directory_returns_dir_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "subdir").mkdir()

            result = ws.stat("subdir")
            assert result["type"] == "dir"
            assert result["size_bytes"] == 0

    def test_stat_missing_returns_missing_type(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            result = ws.stat("missing.txt")
            assert result["type"] == "missing"


class TestFsWorkspaceMkdirs:
    def test_mkdirs_creates_nested_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.mkdirs("a/b/c")

            assert (Path(tmpdir) / "a" / "b" / "c").is_dir()

    def test_mkdirs_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            ws.mkdirs("a/b")
            ws.mkdirs("a/b")

            assert (Path(tmpdir) / "a" / "b").is_dir()


class TestFsWorkspaceMove:
    def test_move_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "src.txt").write_text("content", encoding="utf-8")

            ws.move("src.txt", "dest.txt")

            assert (Path(tmpdir) / "src.txt").exists() is False
            assert (Path(tmpdir) / "dest.txt").read_text(encoding="utf-8") == "content"

    def test_move_with_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "src.txt").write_text("src", encoding="utf-8")
            (Path(tmpdir) / "dest.txt").write_text("dest", encoding="utf-8")

            ws.move("src.txt", "dest.txt", overwrite=True)

            assert (Path(tmpdir) / "dest.txt").read_text(encoding="utf-8") == "src"

    def test_move_without_overwrite_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "src.txt").write_text("src", encoding="utf-8")
            (Path(tmpdir) / "dest.txt").write_text("dest", encoding="utf-8")

            with pytest.raises(FileExistsError):
                ws.move("src.txt", "dest.txt")


class TestFsWorkspaceCopy:
    def test_copy_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "src.txt").write_text("content", encoding="utf-8")

            ws.copy("src.txt", "dest.txt")

            assert (Path(tmpdir) / "src.txt").exists()
            assert (Path(tmpdir) / "dest.txt").read_text(encoding="utf-8") == "content"

    def test_copy_file_with_overwrite(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "src.txt").write_text("src", encoding="utf-8")
            (Path(tmpdir) / "dest.txt").write_text("dest", encoding="utf-8")

            ws.copy("src.txt", "dest.txt", overwrite=True)

            assert (Path(tmpdir) / "dest.txt").read_text(encoding="utf-8") == "src"

    def test_copy_directory(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            subdir = Path(tmpdir) / "src_dir"
            subdir.mkdir()
            (subdir / "file.txt").write_text("nested", encoding="utf-8")

            ws.copy("src_dir", "dest_dir")

            assert (Path(tmpdir) / "dest_dir" / "file.txt").read_text(encoding="utf-8") == "nested"


class TestFsWorkspaceDelete:
    def test_delete_file(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "todelete.txt").write_text("content", encoding="utf-8")

            ws.delete("todelete.txt")

            assert (Path(tmpdir) / "todelete.txt").exists() is False

    def test_delete_directory_without_recursive_raises(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "subdir").mkdir()

            with pytest.raises(IsADirectoryError):
                ws.delete("subdir")

    def test_delete_directory_recursive(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            subdir = Path(tmpdir) / "subdir"
            subdir.mkdir()
            (subdir / "file.txt").write_text("content", encoding="utf-8")

            ws.delete("subdir", recursive=True)

            assert (Path(tmpdir) / "subdir").exists() is False


class TestFsWorkspaceAllowedRoots:
    def test_allowed_roots_returns_list(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            roots = ws.allowed_roots()

            assert isinstance(roots, list)
            assert len(roots) >= 1

    def test_allowed_roots_with_multiple_roots(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir, allowed_roots=[tmpdir, "/tmp"])

            roots = ws.allowed_roots()

            assert len(roots) == 2  # noqa: PLR2004


class TestFsWorkspaceIterFiles:
    def test_iter_files_returns_files_under_base(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "file1.txt").write_text("", encoding="utf-8")
            (Path(tmpdir) / "file2.txt").write_text("", encoding="utf-8")

            files = ws.iter_files(".")

            assert "file1.txt" in files
            assert "file2.txt" in files

    def test_iter_files_excludes_skipped_directories(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            (Path(tmpdir) / "file.txt").write_text("", encoding="utf-8")
            (Path(tmpdir) / "subdir").mkdir(parents=True)
            (Path(tmpdir) / "subdir" / "nested.txt").write_text("", encoding="utf-8")
            (Path(tmpdir) / ".git").mkdir(parents=True)
            (Path(tmpdir) / ".git" / "config").write_text("", encoding="utf-8")

            files = ws.iter_files(".")

            assert "file.txt" in files
            assert "subdir/nested.txt" in files
            assert ".git/config" not in files

    def test_iter_files_nonexistent_base_returns_empty(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)

            files = ws.iter_files("nonexistent")

            assert files == ()

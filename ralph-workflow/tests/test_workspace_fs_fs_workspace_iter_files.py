"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path


class TestFsWorkspaceIterFiles:
    def test_iter_files_returns_files_under_base(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "file1.txt").write_text("", encoding="utf-8")
        (tmp_path / "file2.txt").write_text("", encoding="utf-8")

        files = ws.iter_files(".")

        assert "file1.txt" in files
        assert "file2.txt" in files

    def test_iter_files_excludes_skipped_directories(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "file.txt").write_text("", encoding="utf-8")
        (tmp_path / "subdir").mkdir(parents=True)
        (tmp_path / "subdir" / "nested.txt").write_text("", encoding="utf-8")
        (tmp_path / ".git").mkdir(parents=True)
        (tmp_path / ".git" / "config").write_text("", encoding="utf-8")

        files = ws.iter_files(".")

        assert "file.txt" in files
        assert "subdir/nested.txt" in files
        assert ".git/config" not in files

    def test_iter_files_nonexistent_base_returns_empty(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        files = ws.iter_files("nonexistent")

        assert files == ()

    def test_iter_files_excludes_build_output(self, tmp_path: Path) -> None:
        """S-4 regression: generated build output is outside the traversal surface."""
        ws = FsWorkspace(tmp_path)
        (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
        (tmp_path / "build").mkdir()
        (tmp_path / "build" / "generated.py").write_text("value = 2\n", encoding="utf-8")

        files = ws.iter_files(".")

        assert "source.py" in files
        assert "build/generated.py" not in files

    def test_iter_files_skips_a_generated_base_directory(self, tmp_path: Path) -> None:
        """S-4 regression: an explicit generated base cannot bypass the skip set."""
        ws = FsWorkspace(tmp_path)
        (tmp_path / ".git").mkdir()
        (tmp_path / ".git" / "config").write_text("[core]\n", encoding="utf-8")

        assert ws.iter_files(".git") == ()

    def test_iter_files_does_not_follow_a_symlink_cycle(self, tmp_path: Path) -> None:
        """R2: a symlink cycle inside the traversal base cannot cause unbounded recursion."""
        ws = FsWorkspace(tmp_path)
        (tmp_path / "source.py").write_text("value = 1\n", encoding="utf-8")
        sub = tmp_path / "sub"
        sub.mkdir()
        (sub / "nested.txt").write_text("nested\n", encoding="utf-8")
        # Self-referential symlink that would otherwise form a cycle.
        sub_loop = sub / "loop"
        sub_loop.symlink_to(sub, target_is_directory=True)

        files = ws.iter_files(".")

        # ``os.walk`` does not follow directory symlinks by default, so the
        # cycle resolves without recursion. The visible files are stable.
        assert "source.py" in files
        assert "sub/nested.txt" in files
        assert "sub/loop/nested.txt" not in files
        # Bounded number of files proves the traversal terminated.
        assert len(files) == 2

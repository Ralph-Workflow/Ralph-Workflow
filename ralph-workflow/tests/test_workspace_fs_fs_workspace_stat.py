"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

import tempfile
from pathlib import Path

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceStat:
    def test_stat_file_returns_metadata(self) -> None:
        with tempfile.TemporaryDirectory() as tmpdir:
            ws = FsWorkspace(tmpdir)
            file_path = Path(tmpdir) / "file.txt"
            file_path.write_text("hello", encoding="utf-8")

            result = ws.stat("file.txt")
            assert result["type"] == "file"
            assert result["size_bytes"] == 5
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

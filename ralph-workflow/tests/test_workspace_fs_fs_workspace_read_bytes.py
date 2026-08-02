"""Tests for ralph/workspace/fs.py — Filesystem workspace implementation."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.workspace.fs import FsWorkspace


class TestFsWorkspaceReadBytes:
    def test_full_file_read_no_offset(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        content = "Hello, World!"
        (tmp_path / "file.txt").write_text(content, encoding="utf-8")

        text, meta = ws.read_bytes("file.txt")

        assert text == content
        assert meta["total_bytes"] == len(content.encode("utf-8"))
        assert meta["returned_bytes"] == len(content.encode("utf-8"))
        assert meta["truncated"] is False

    def test_byte_offset_read(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "file.txt").write_text("Hello, World!", encoding="utf-8")

        text, meta = ws.read_bytes("file.txt", offset=7)

        assert text == "World!"
        assert meta["returned_bytes"] == len(b"World!")
        assert meta["truncated"] is False

    def test_byte_limit_read(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "file.txt").write_text("Hello, World!", encoding="utf-8")

        text, meta = ws.read_bytes("file.txt", limit=5)

        assert text == "Hello"
        assert meta["returned_bytes"] == 5
        assert meta["truncated"] is True

    def test_offset_and_limit_read(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        (tmp_path / "file.txt").write_text("Hello, World!", encoding="utf-8")

        text, meta = ws.read_bytes("file.txt", offset=7, limit=5)

        assert text == "World"
        assert meta["returned_bytes"] == 5
        assert meta["truncated"] is True

    def test_missing_file_raises_file_not_found(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)

        with pytest.raises(FileNotFoundError):
            ws.read_bytes("nonexistent.txt")

    def test_read_bytes_regression_uses_metadata_to_detect_a_missing_file(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """S-5: a byte-window read obtains existence and size in one metadata access."""
        ws = FsWorkspace(tmp_path)
        target = tmp_path / "missing.txt"
        original_exists = Path.exists

        def guarded_exists(candidate: Path) -> bool:
            if candidate == target:
                raise AssertionError("read_bytes must not probe existence before stat")
            return original_exists(candidate)

        monkeypatch.setattr(Path, "exists", guarded_exists)

        with pytest.raises(FileNotFoundError, match=r"File not found: missing\.txt"):
            ws.read_bytes("missing.txt")

    def test_total_bytes_reflects_file_size(self, tmp_path: Path) -> None:
        ws = FsWorkspace(tmp_path)
        content = "A" * 1000
        (tmp_path / "large.txt").write_text(content, encoding="utf-8")

        _, meta = ws.read_bytes("large.txt", limit=100)

        assert meta["total_bytes"] == 1000
        assert meta["returned_bytes"] == 100
        assert meta["truncated"] is True

    def test_read_bytes_regression_rejects_an_unbounded_large_read(self, tmp_path: Path) -> None:
        """S-4: a full byte read is size-checked before opening the file."""
        ws = FsWorkspace(tmp_path)
        (tmp_path / "large.txt").write_text("A" * 11, encoding="utf-8")

        with pytest.raises(ValueError, match="File too large for read_bytes"):
            ws.read_bytes("large.txt", max_bytes=10)

    def test_read_bytes_allows_a_bounded_window_for_a_large_file(self, tmp_path: Path) -> None:
        """S-4: requesting a small byte window does not require a full-file load."""
        ws = FsWorkspace(tmp_path)
        (tmp_path / "large.txt").write_text("abcdefghijk", encoding="utf-8")

        text, meta = ws.read_bytes("large.txt", limit=4, max_bytes=10)

        assert text == "abcd"
        assert meta == {"total_bytes": 11, "returned_bytes": 4, "truncated": True}

    def test_read_bytes_allows_a_short_tail_without_an_explicit_limit(self, tmp_path: Path) -> None:
        """S-4: a read to end is bounded by the bytes after its offset, not file size."""
        ws = FsWorkspace(tmp_path)
        (tmp_path / "large.txt").write_text("abcdefghijk", encoding="utf-8")

        text, meta = ws.read_bytes("large.txt", offset=8, max_bytes=10)

        assert text == "ijk"
        assert meta == {"total_bytes": 11, "returned_bytes": 3, "truncated": False}

    def test_read_bytes_regression_rejects_a_negative_window_limit(self, tmp_path: Path) -> None:
        """S-4: a negative limit cannot bypass the requested-window size guard."""
        ws = FsWorkspace(tmp_path)
        (tmp_path / "large.txt").write_text("abcdefghijk", encoding="utf-8")

        with pytest.raises(ValueError, match="limit must not be negative"):
            ws.read_bytes("large.txt", limit=-1, max_bytes=10)

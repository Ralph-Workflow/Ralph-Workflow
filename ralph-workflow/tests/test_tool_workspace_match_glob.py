"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from ralph.mcp.tools.workspace import (
    match_glob,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestMatchGlob:
    def test_simple_asterisk_matches_all(self) -> None:
        assert match_glob("file.txt", "*") is True
        assert match_glob("dir/file.txt", "*") is True

    def test_glob_matches_single_segment(self) -> None:
        assert match_glob("main.py", "*.py") is True
        assert match_glob("test.py", "*.py") is True
        assert match_glob("main.txt", "*.py") is False

    def test_double_asterisk_matches_nested(self) -> None:
        assert match_glob("src/main.py", "**/*.py") is True
        assert match_glob("a/b/c/file.py", "**/*.py") is True
        assert match_glob("file.py", "**/*.py") is True

    def test_exact_match(self) -> None:
        assert match_glob("test.py", "test.py") is True
        assert match_glob("test.py", "*.py") is True

    def test_question_mark_matches_single_char(self) -> None:
        assert match_glob("file1.py", "file?.py") is True
        assert match_glob("file12.py", "file??.py") is True
        assert match_glob("file.py", "file?.py") is False

    def test_path_with_directory(self) -> None:
        assert match_glob("src/main.py", "src/*.py") is True
        assert match_glob("src/main.c", "src/*.py") is False

    def test_nested_path_with_double_asterisk(self) -> None:
        assert match_glob("src/dir/main.py", "src/**/*.py") is True
        assert match_glob("src/a/b/c/file.py", "src/**/*.py") is True

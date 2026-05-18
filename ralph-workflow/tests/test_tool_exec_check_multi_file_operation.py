"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

from ralph.mcp.tools.exec import (
    check_multi_file_operation,
)

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestCheckMultiFileOperation:
    def test_find_exec_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("find", [".", "-exec", "rm", "{}"])
        assert reason is not None
        assert "find" in reason.lower()

    def test_find_delete_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("find", [".", "-delete"])
        assert reason is not None

    def test_xargs_rm_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("xargs", ["rm", "-rf"])
        assert reason is not None
        assert "xargs" in reason.lower()

    def test_sed_inplace_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("sed", ["-i", "s/foo/bar/", "file.txt"])
        assert reason is not None
        assert "sed" in reason.lower()

    def test_awk_inplace_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("awk", ["-i", "{print}", "file.txt"])
        assert reason is not None

    def test_rename_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("rename", ["foo", "bar", "*.txt"])
        assert reason is not None

    def test_chmod_recursive_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("chmod", ["-R", "755", "/path"])
        assert reason is not None
        assert "chmod" in reason.lower()

    def test_cp_recursive_with_glob_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("cp", ["-rf", "*.txt", "/dest"])
        assert reason is not None

    def test_tar_extract_in_place_is_blacklisted(self) -> None:
        reason = check_multi_file_operation("tar", ["-xf", "archive.tar.gz"])
        assert reason is not None

    def test_allowed_command_returns_none(self) -> None:
        reason = check_multi_file_operation("cat", ["file.txt"])
        assert reason is None

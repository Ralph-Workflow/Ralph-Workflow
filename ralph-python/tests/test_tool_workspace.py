"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from dataclasses import dataclass
from unittest.mock import MagicMock

import pytest

from ralph.mcp.tool_coordination import CapabilityDeniedError, InvalidParamsError, ToolError
from ralph.mcp.tool_workspace import (
    WORKSPACE_READ_CAPABILITY,
    WORKSPACE_WRITE_EPHEMERAL_CAPABILITY,
    WORKSPACE_WRITE_TRACKED_CAPABILITY,
    _check_edit_area_restriction,
    _is_parallel_worker,
    _is_path_git_tracked,
    _is_policy_approved,
    _join_path,
    _list_dir_entries,
    _list_dir_flat,
    _normalize_relative_path,
    handle_list_directory,
    handle_list_directory_recursive,
    handle_read_file,
    handle_search_files,
    handle_write_file,
    required_string_param,
)


@dataclass
class MockSession:
    allowed_capability: str | None = None
    is_parallel_worker: bool = False
    session_id: str = "test-session"

    def check_capability(self, capability: str) -> object:
        return capability == self.allowed_capability

# =============================================================================
# required_string_param tests
# =============================================================================


class TestRequiredStringParam:
    def test_returns_string_value(self) -> None:
        params: dict[str, object] = {"path": "/some/path"}
        result = required_string_param(params, "path")
        assert result == "/some/path"

    def test_missing_param_raises(self) -> None:
        params: dict[str, object] = {}
        with pytest.raises(InvalidParamsError):
            required_string_param(params, "path")

    def test_non_string_param_raises(self) -> None:
        params: dict[str, object] = {"path": 123}
        with pytest.raises(InvalidParamsError):
            required_string_param(params, "path")


# =============================================================================
# Path normalization tests
# =============================================================================


class TestNormalizeRelativePath:
    def test_empty_path_returns_empty(self) -> None:
        assert _normalize_relative_path("") == ""

    def test_dot_returns_empty(self) -> None:
        assert _normalize_relative_path(".") == ""

    def test_slash_handled(self) -> None:
        result = _normalize_relative_path("path/to/file")
        assert result == "path/to/file"

    def test_windows_backslash_preserved(self) -> None:
        # PurePosixPath preserves backslashes as literal characters
        result = _normalize_relative_path("path\\to\\file")
        assert result == "path\\to\\file"

    def test_leading_slash_preserved(self) -> None:
        # PurePosixPath preserves leading slash in POSIX paths
        result = _normalize_relative_path("/absolute/path")
        assert result.startswith("/")


class TestJoinPath:
    def test_empty_base_returns_normalized_entry(self) -> None:
        assert _join_path("", "file.txt") == "file.txt"

    def test_joins_with_posix_separator(self) -> None:
        assert _join_path("dir", "file.txt") == "dir/file.txt"

    def test_multiple_segments(self) -> None:
        assert _join_path("a/b", "c/d") == "a/b/c/d"


# =============================================================================
# _is_policy_approved tests
# =============================================================================


class TestIsPolicyApproved:
    def test_true_is_approved(self) -> None:
        assert _is_policy_approved(True) is True

    def test_string_approved_is_approved(self) -> None:
        assert _is_policy_approved("approved") is True
        assert _is_policy_approved("allow") is True
        assert _is_policy_approved("allowed") is True

    def test_string_approved_strips_whitespace(self) -> None:
        assert _is_policy_approved("  approved  ") is True

    def test_other_strings_not_approved(self) -> None:
        assert _is_policy_approved("denied") is False
        assert _is_policy_approved("reject") is False

    def test_object_with_name_attribute(self) -> None:
        class Outcome:
            name = "approved"

        assert _is_policy_approved(Outcome()) is True

    def test_object_with_value_attribute(self) -> None:
        class Outcome:
            value = "allow"

        assert _is_policy_approved(Outcome()) is True

    def test_object_with_status_attribute(self) -> None:
        class Outcome:
            status = "allowed"

        assert _is_policy_approved(Outcome()) is True

    def test_none_is_not_approved(self) -> None:
        assert _is_policy_approved(None) is False


# =============================================================================
# _is_parallel_worker tests
# =============================================================================


class TestIsParallelWorker:
    def test_false_flag_returns_false(self) -> None:
        class Session:
            is_parallel_worker = False

        assert _is_parallel_worker(Session()) is False

    def test_true_flag_returns_true(self) -> None:
        class Session:
            is_parallel_worker = True

        assert _is_parallel_worker(Session()) is True

    def test_callable_true_returns_true(self) -> None:
        class Session:
            def is_parallel_worker(self) -> bool:
                return True

        assert _is_parallel_worker(Session()) is True

    def test_callable_false_returns_false(self) -> None:
        class Session:
            def is_parallel_worker(self) -> bool:
                return False

        assert _is_parallel_worker(Session()) is False

    def test_callable_raises_type_error_returns_false(self) -> None:
        class Session:
            def is_parallel_worker(self) -> bool:
                raise TypeError("not a bool")

        assert _is_parallel_worker(Session()) is False

    def test_missing_attribute_returns_false(self) -> None:
        class Session:
            pass

        assert _is_parallel_worker(Session()) is False


# =============================================================================
# _check_edit_area_restriction tests
# =============================================================================


class TestCheckEditAreaRestriction:
    def test_non_parallel_worker_passes(self) -> None:
        class Session:
            is_parallel_worker = False

        _check_edit_area_restriction(Session(), "/any/path")

    def test_parallel_worker_without_checker_passes(self) -> None:
        class Session:
            is_parallel_worker = True

        _check_edit_area_restriction(Session(), "/any/path")

    def test_parallel_worker_with_approved_checker_passes(self) -> None:
        class Session:
            is_parallel_worker = True

            def check_edit_area(self, path: str) -> bool:
                return True

        _check_edit_area_restriction(Session(), "/any/path")

    def test_parallel_worker_with_denied_checker_raises(self) -> None:
        class Session:
            is_parallel_worker = True

            def check_edit_area(self, path: str) -> bool:
                return False

        with pytest.raises(CapabilityDeniedError):
            _check_edit_area_restriction(Session(), "/any/path")


# =============================================================================
# _is_path_git_tracked tests
# =============================================================================


class TestIsPathGitTracked:
    def test_empty_path_returns_false(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = False
        assert _is_path_git_tracked(ws, "") is False

    def test_nonexistent_path_returns_false(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = False
        assert _is_path_git_tracked(ws, "file.txt") is False

    def test_existing_file_not_in_excluded_paths_returns_true(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = True
        assert _is_path_git_tracked(ws, "src/main.py") is True

    def test_file_in_agent_dir_returns_false(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = True
        assert _is_path_git_tracked(ws, ".agent/config.yaml") is False

    def test_file_with_target_substring_but_not_excluded_path(self) -> None:
        # "my_target/main" doesn't contain "/target/" so it passes
        ws = MagicMock()
        ws.exists.return_value = True
        assert _is_path_git_tracked(ws, "my_target/main") is True

    def test_file_in_node_modules_returns_false(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = True
        assert _is_path_git_tracked(ws, "node_modules/lodash") is False

    def test_backslash_path_normalized(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = True
        result = _is_path_git_tracked(ws, "src\\main.py")
        assert result is True


# =============================================================================
# _list_dir_entries tests
# =============================================================================


class TestListDirEntries:
    def test_returns_list_from_workspace(self) -> None:
        ws = MagicMock()
        ws.list_dir.return_value = ["file1.txt", "file2.txt"]

        result = _list_dir_entries(ws, "")
        assert result == ["file1.txt", "file2.txt"]

    def test_propagates_workspace_exception(self) -> None:
        ws = MagicMock()
        ws.list_dir.side_effect = RuntimeError("disk error")

        with pytest.raises(ToolError):
            _list_dir_entries(ws, "")


# =============================================================================
# _list_dir_flat tests
# =============================================================================


class TestListDirFlat:
    def test_lists_files_and_dirs(self) -> None:
        ws = MagicMock()
        ws.list_dir.return_value = ["file.txt", "subdir"]
        ws.is_dir.side_effect = lambda p: p == "subdir"

        result = _list_dir_flat(ws, "")
        assert "Directory:" in result
        assert "[FILE]" in result
        assert "[DIR]" in result


# =============================================================================
# handle_read_file tests
# =============================================================================


class TestHandleReadFile:
    def test_reads_file_content(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "file contents"

        result = handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "file.txt"})
        assert "file contents" in result.content[0].text
        assert result.is_error is False

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_read_file(MockSession(), ws, {"path": "file.txt"})

    def test_missing_path_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {})

    def test_file_not_found_raises_tool_error(self) -> None:
        ws = MagicMock()
        ws.read.side_effect = FileNotFoundError("not found")

        with pytest.raises(ToolError):
            handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "missing.txt"})


# =============================================================================
# handle_list_directory tests
# =============================================================================


class TestHandleListDirectory:
    def test_lists_directory_flat(self) -> None:
        ws = MagicMock()
        ws.list_dir.return_value = ["a.txt", "b.txt"]
        ws.is_dir.side_effect = lambda p: False

        result = handle_list_directory(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "."})
        assert result.is_error is False
        assert "Directory:" in result.content[0].text

    def test_lists_directory_recursive(self) -> None:
        ws = MagicMock()
        ws.list_dir.return_value = []
        ws.is_dir.side_effect = lambda p: False

        result = handle_list_directory(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": ".", "recursive": True},
        )
        assert result.is_error is False
        assert "Directory (recursive):" in result.content[0].text


# =============================================================================
# handle_list_directory_recursive tests
# =============================================================================


class TestHandleListDirectoryRecursive:
    def test_returns_recursive_listing(self) -> None:
        ws = MagicMock()
        ws.list_dir.return_value = []
        ws.is_dir.side_effect = lambda p: False

        result = handle_list_directory_recursive(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "."},
        )
        assert result.is_error is False
        assert "Directory (recursive):" in result.content[0].text


# =============================================================================
# handle_search_files tests
# =============================================================================


class TestHandleSearchFiles:
    def test_search_finds_matching_files(self) -> None:
        ws = MagicMock()
        ws.list_dir.return_value = ["main.py", "test.py"]

        def is_dir(path: str) -> bool:
            # "." as base means entries are flat
            return False

        def is_file(path: str) -> bool:
            return path in ("main.py", "test.py")

        def exists(path: str) -> bool:
            return path in ("main.py", "test.py")

        ws.is_dir.side_effect = is_dir
        ws.is_file.side_effect = is_file
        ws.exists.side_effect = exists

        result = handle_search_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "main", "path": "."},
        )
        assert result.is_error is False
        assert "main.py" in result.content[0].text

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_search_files(MockSession(), ws, {"pattern": "*", "path": "."})


# =============================================================================
# handle_write_file tests
# =============================================================================


class TestHandleWriteFile:
    def test_writes_new_file_as_ephemeral(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = False
        ws.write.return_value = None

        result = handle_write_file(
            MockSession(WORKSPACE_WRITE_EPHEMERAL_CAPABILITY),
            ws,
            {"path": "new.txt", "content": "hello"},
        )
        assert result.is_error is False
        assert "new.txt" in result.content[0].text
        ws.write.assert_called_once()

    def test_writes_git_tracked_file_with_tracked_capability(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = True  # File exists = git tracked
        ws.write.return_value = None

        session = MockSession(WORKSPACE_WRITE_TRACKED_CAPABILITY)
        result = handle_write_file(session, ws, {"path": "src/main.py", "content": "code"})
        assert result.is_error is False
        assert session.check_capability(WORKSPACE_WRITE_TRACKED_CAPABILITY) is True

    def test_missing_path_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_write_file(
                MockSession(WORKSPACE_WRITE_EPHEMERAL_CAPABILITY),
                ws,
                {"content": "hello"},
            )

    def test_missing_content_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_write_file(
                MockSession(WORKSPACE_WRITE_EPHEMERAL_CAPABILITY),
                ws,
                {"path": "file.txt"},
            )

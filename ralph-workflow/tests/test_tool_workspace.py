"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

import base64
import json
import tempfile
from dataclasses import dataclass, field
from pathlib import Path
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.multimodal.artifacts import (
    AudioContent,
    DocumentContent,
    PdfContent,
    ResourceReferenceContent,
    VideoContent,
)
from ralph.mcp.multimodal.capabilities import UNKNOWN_IDENTITY, MultimodalModelIdentity
from ralph.mcp.multimodal.errors import MultimodalFailureKind
from ralph.mcp.multimodal.resources import MediaManifest
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    ImageContent,
    InvalidParamsError,
    ToolContent,
    ToolError,
    _read_env_value,
)
from ralph.mcp.tools.workspace import (
    _FULL_READ_DEFAULT_MAX_BYTES,
    WORKSPACE_DELETE_CAPABILITY,
    WORKSPACE_EDIT_CAPABILITY,
    WORKSPACE_METADATA_READ_CAPABILITY,
    WORKSPACE_READ_CAPABILITY,
    WORKSPACE_WRITE_EPHEMERAL_CAPABILITY,
    WORKSPACE_WRITE_TRACKED_CAPABILITY,
    _check_edit_area_restriction,
    _infer_image_mime_type,
    _is_parallel_worker,
    _is_path_git_tracked,
    _is_policy_approved,
    _join_path,
    _list_dir_entries,
    _list_dir_flat,
    _match_glob,
    _normalize_relative_path,
    handle_append_file,
    handle_copy_file,
    handle_create_directory,
    handle_delete_path,
    handle_directory_tree,
    handle_edit_file,
    handle_grep_files,
    handle_list_allowed_roots,
    handle_list_directory,
    handle_list_directory_recursive,
    handle_move_file,
    handle_read_file,
    handle_read_image,
    handle_read_media,
    handle_read_multiple_files,
    handle_search_files,
    handle_stat,
    handle_write_file,
    required_string_param,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


@dataclass
class MockSession:
    allowed_capability: str | None = None
    is_parallel_worker: bool = False
    session_id: str = "test-session"

    def check_capability(self, capability: str) -> object:
        return capability == self.allowed_capability

    def check_edit_area(self, path: str) -> object:
        return True


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
        result = _normalize_relative_path("path\\to\\file")
        assert result == "path\\to\\file"

    def test_leading_slash_preserved(self) -> None:
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
# _match_glob tests
# =============================================================================


class TestMatchGlob:
    def test_simple_asterisk_matches_all(self) -> None:
        assert _match_glob("file.txt", "*") is True
        assert _match_glob("dir/file.txt", "*") is True

    def test_glob_matches_single_segment(self) -> None:
        assert _match_glob("main.py", "*.py") is True
        assert _match_glob("test.py", "*.py") is True
        assert _match_glob("main.txt", "*.py") is False

    def test_double_asterisk_matches_nested(self) -> None:
        assert _match_glob("src/main.py", "**/*.py") is True
        assert _match_glob("a/b/c/file.py", "**/*.py") is True
        assert _match_glob("file.py", "**/*.py") is True

    def test_exact_match(self) -> None:
        assert _match_glob("test.py", "test.py") is True
        assert _match_glob("test.py", "*.py") is True

    def test_question_mark_matches_single_char(self) -> None:
        assert _match_glob("file1.py", "file?.py") is True
        assert _match_glob("file12.py", "file??.py") is True
        assert _match_glob("file.py", "file?.py") is False

    def test_path_with_directory(self) -> None:
        assert _match_glob("src/main.py", "src/*.py") is True
        assert _match_glob("src/main.c", "src/*.py") is False

    def test_nested_path_with_double_asterisk(self) -> None:
        assert _match_glob("src/dir/main.py", "src/**/*.py") is True
        assert _match_glob("src/a/b/c/file.py", "src/**/*.py") is True


# =============================================================================
# handle_read_file tests
# =============================================================================


class TestHandleReadFile:
    def test_reads_file_content(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "file contents"

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "file.txt"}
        )
        assert "file contents" in cast("ToolContent", result.content[0]).text
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
            handle_read_file(
                MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "missing.txt"}
            )


class TestHandleReadFilePartial:
    """Tests for partial read variants."""

    def test_head_returns_first_n_lines(self) -> None:
        ws = MagicMock()
        ws.read_lines.return_value = (
            "line1\nline2\n",
            {"total_lines": 5, "returned_lines": 2, "truncated": True},
        )

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "file.txt", "head": 2},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["content"] == "line1\nline2\n"
        assert payload["returned_lines"] == 2  # noqa: PLR2004
        assert payload["truncated"] is True

    def test_tail_returns_last_n_lines(self) -> None:
        ws = MagicMock()
        ws.read_lines.return_value = (
            "line4\nline5\n",
            {"total_lines": 5, "returned_lines": 2, "truncated": True},
        )

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "file.txt", "tail": 2},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["content"] == "line4\nline5\n"

    def test_line_start_and_end_returns_range(self) -> None:
        ws = MagicMock()
        ws.read_lines.return_value = (
            "line2\nline3\n",
            {"total_lines": 5, "returned_lines": 2, "truncated": False},
        )

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "file.txt", "line_start": 2, "line_end": 3},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["content"] == "line2\nline3\n"

    def test_offset_and_limit_uses_byte_window_read(self) -> None:
        ws = MagicMock()
        ws.read_bytes.return_value = (
            "some content",
            {"total_bytes": 200, "returned_bytes": 100, "truncated": True},
        )

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "file.txt", "offset": 0, "limit": 100},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["content"] == "some content"
        assert payload["total_bytes"] == 200  # noqa: PLR2004
        assert payload["returned_bytes"] == 100  # noqa: PLR2004
        assert payload["truncated"] is True

    def test_offset_only_reads_from_byte_position(self) -> None:
        ws = MagicMock()
        ws.read_bytes.return_value = (
            "remainder content",
            {"total_bytes": 100, "returned_bytes": 83, "truncated": False},
        )

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "file.txt", "offset": 17},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["content"] == "remainder content"
        assert payload["total_bytes"] == 100  # noqa: PLR2004
        ws.read_bytes.assert_called_once()
        _, kwargs = ws.read_bytes.call_args
        assert kwargs["offset"] == 17  # noqa: PLR2004
        assert kwargs["limit"] is None

    def test_conflicting_params_raise_invalid_params(self) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_read_file(
                MockSession(WORKSPACE_READ_CAPABILITY),
                ws,
                {"path": "file.txt", "head": 2, "offset": 5},
            )

    def test_line_range_conflicts_with_offset_limit_raise_invalid_params(
        self,
    ) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_read_file(
                MockSession(WORKSPACE_READ_CAPABILITY),
                ws,
                {"path": "file.txt", "line_start": 1, "line_end": 5, "offset": 0, "limit": 10},
            )

    def test_line_range_with_inert_zero_offset_and_limit_succeeds(
        self,
    ) -> None:
        """Regression: brokers that send all optional fields with zero defaults must not fail."""
        ws = MagicMock()
        ws.read_lines.return_value = (
            "line2\nline3\n",
            {"total_lines": 5, "returned_lines": 2, "truncated": True},
        )

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "file.txt", "line_start": 2, "line_end": 3, "offset": 0, "limit": 0},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["content"] == "line2\nline3\n"
        ws.read_lines.assert_called_once()
        _, kwargs = ws.read_lines.call_args
        assert kwargs["start"] == 2  # noqa: PLR2004
        assert kwargs["end"] == 3  # noqa: PLR2004

    def test_head_with_inert_zero_offset_and_limit_succeeds(
        self,
    ) -> None:
        """Regression: head read must work when broker also sends offset=0, limit=0."""
        ws = MagicMock()
        ws.read_lines.return_value = (
            "first\nsecond\n",
            {"total_lines": 10, "returned_lines": 2, "truncated": True},
        )

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "file.txt", "head": 2, "offset": 0, "limit": 0},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["content"] == "first\nsecond\n"
        ws.read_lines.assert_called_once()
        _, kwargs = ws.read_lines.call_args
        assert kwargs["head"] == 2  # noqa: PLR2004

    def test_tail_with_inert_zero_offset_succeeds(
        self,
    ) -> None:
        """Regression: tail read must work when broker also sends offset=0."""
        ws = MagicMock()
        ws.read_lines.return_value = (
            "last\nline\n",
            {"total_lines": 10, "returned_lines": 2, "truncated": True},
        )

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "file.txt", "tail": 2, "offset": 0, "limit": 0},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["content"] == "last\nline\n"
        ws.read_lines.assert_called_once()
        _, kwargs = ws.read_lines.call_args
        assert kwargs["tail"] == 2  # noqa: PLR2004

    def test_offset_zero_with_positive_limit_uses_byte_window(
        self,
    ) -> None:
        """Regression: offset=0 with limit>0 must select byte-window mode."""
        ws = MagicMock()
        ws.read_bytes.return_value = (
            "hello",
            {"total_bytes": 1000, "returned_bytes": 5, "truncated": True},
        )

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "file.txt", "offset": 0, "limit": 100},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["content"] == "hello"
        ws.read_bytes.assert_called_once()
        _, kwargs = ws.read_bytes.call_args
        assert kwargs["offset"] == 0
        assert kwargs["limit"] == 100  # noqa: PLR2004


class TestHandleReadFileNonUtf8:
    """Tests for structured non-UTF-8 error in handle_read_file."""

    def test_returns_structured_error_for_unicode_decode_error(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.side_effect = UnicodeDecodeError("utf-8", b"\xff", 0, 1, "invalid start byte")

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "binary.bin"}
        )
        assert result.is_error is True
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["status"] == "binary_or_invalid_utf8"
        assert payload["path"] == "binary.bin"
        assert payload["byte_offset"] == 0

    def test_propagates_other_exceptions_as_tool_error(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.side_effect = RuntimeError("unexpected disk error")

        with pytest.raises(ToolError):
            handle_read_file(
                MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "file.txt"}
            )


class TestHandleReadFileFullReadTruncation:
    """Tests for oversize truncation in handle_read_file."""

    def test_small_file_returns_plain_text(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.return_value = "small content"

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "small.txt"}
        )
        assert result.is_error is False
        assert cast("ToolContent", result.content[0]).text == "small content"

    def test_oversize_file_returns_truncation_envelope(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "file", "size_bytes": 10_000_000}
        ws.read_lines.return_value = (
            "first 5MB worth",
            {"total_lines": 50000, "returned_lines": 19531, "truncated": True},
        )

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "large.txt"}
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["truncated"] is True
        assert payload["total_bytes"] == 10_000_000  # noqa: PLR2004
        assert payload["max_bytes"] == _FULL_READ_DEFAULT_MAX_BYTES
        assert payload["reason"] == "oversize"
        assert "content" in payload

    def test_explicit_max_bytes_override_respected(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "file", "size_bytes": 2000}
        ws.read_lines.return_value = (
            "truncated content",
            {"total_lines": 10, "returned_lines": 3, "truncated": True},
        )

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "file.txt", "max_bytes": 1000},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["max_bytes"] == 1000  # noqa: PLR2004
        assert payload["truncated"] is True

    def test_dir_path_falls_through_to_workspace_read(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "dir"}
        ws.read.side_effect = IsADirectoryError("is a directory")

        with pytest.raises(ToolError):
            handle_read_file(
                MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "somedir"}
            )


# =============================================================================
# handle_read_multiple_files tests
# =============================================================================


class TestHandleReadMultipleFiles:
    def test_reads_multiple_files(self) -> None:
        ws = MagicMock()
        ws.read.side_effect = ["content1", "content2"]

        result = handle_read_multiple_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"paths": ["file1.txt", "file2.txt"]},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert len(payload["files"]) == 2  # noqa: PLR2004
        assert payload["files"][0]["content"] == "content1"
        assert payload["files"][1]["content"] == "content2"

    def test_partial_failure_returns_error_per_file(self) -> None:
        ws = MagicMock()
        ws.read.side_effect = ["content1", FileNotFoundError("not found")]

        result = handle_read_multiple_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"paths": ["file1.txt", "missing.txt"]},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["files"][0]["content"] == "content1"
        assert "error" in payload["files"][1]

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_read_multiple_files(MockSession(), ws, {"paths": ["file.txt"]})

    def test_non_list_paths_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_read_multiple_files(
                MockSession(WORKSPACE_READ_CAPABILITY), ws, {"paths": "not a list"}
            )


# =============================================================================
# handle_stat tests
# =============================================================================


class TestHandleStat:
    def test_stat_returns_metadata(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {
            "type": "file",
            "size_bytes": 100,
            "created_unix": 123456.0,
            "modified_unix": 789012.0,
            "mode": 33188,
        }

        result = handle_stat(
            MockSession(WORKSPACE_METADATA_READ_CAPABILITY), ws, {"path": "file.txt"}
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["type"] == "file"
        assert payload["size_bytes"] == 100  # noqa: PLR2004

    def test_stat_missing_file(self) -> None:
        ws = MagicMock()
        ws.stat.return_value = {"type": "missing"}

        result = handle_stat(
            MockSession(WORKSPACE_METADATA_READ_CAPABILITY),
            ws,
            {"path": "missing.txt"},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["type"] == "missing"

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_stat(MockSession(), ws, {"path": "file.txt"})


# =============================================================================
# handle_list_allowed_roots tests
# =============================================================================


class TestHandleListAllowedRoots:
    def test_returns_allowed_roots(self) -> None:
        ws = MagicMock()
        ws.allowed_roots.return_value = ["/workspace", "/project"]

        result = handle_list_allowed_roots(
            MockSession(WORKSPACE_READ_CAPABILITY), ws, {}
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["allowed_roots"] == ["/workspace", "/project"]

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_list_allowed_roots(MockSession(), ws, {})


# =============================================================================
# handle_list_directory tests
# =============================================================================


class TestHandleListDirectory:
    def test_lists_directory_flat(self) -> None:
        ws = MagicMock()
        ws.list_dir.return_value = ["a.txt", "b.txt"]
        ws.is_dir.side_effect = lambda p: False

        result = handle_list_directory(
            MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "."}
        )
        assert result.is_error is False
        assert "Directory:" in cast("ToolContent", result.content[0]).text

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
        assert "Directory (recursive):" in cast("ToolContent", result.content[0]).text


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
        assert "Directory (recursive):" in cast("ToolContent", result.content[0]).text

    def test_skips_heavy_directories_and_nested_worktrees(self) -> None:
        ws = MagicMock()
        listings = {
            "": [".git", "src", "target", "wt-feature"],
            "src": ["main.py"],
            ".git": ["objects"],
            "target": ["debug"],
            "wt-feature": ["scratch.txt"],
        }
        directories = {
            ".git",
            ".git/objects",
            "src",
            "target",
            "target/debug",
            "wt-feature",
        }

        ws.list_dir.side_effect = lambda path: listings.get(path, [])
        ws.is_dir.side_effect = lambda path: path in directories
        ws.exists.side_effect = lambda path: path == "wt-feature/.git"

        result = handle_list_directory_recursive(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "."},
        )

        text = cast("ToolContent", result.content[0]).text
        assert "src/main.py" in text
        assert ".git/objects" not in text
        assert "target/debug" not in text
        assert "wt-feature/scratch.txt" not in text


# =============================================================================
# handle_search_files tests
# =============================================================================


class TestHandleSearchFiles:
    def test_search_finds_matching_files(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("main.py", "test.py")
        ws.is_dir.return_value = False
        ws.is_file.return_value = True

        result = handle_search_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "*.py", "path": "."},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert "main.py" in payload["matches"]
        assert "test.py" in payload["matches"]

    def test_search_with_exclude(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file.py", "test_file.py")

        result = handle_search_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "*.py", "path": ".", "exclude": ["test_*.py"]},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert "file.py" in payload["matches"]
        assert "test_file.py" not in payload["matches"]

    def test_search_with_limit(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file1.py", "file2.py", "file3.py")

        result = handle_search_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "*.py", "path": ".", "limit": 2},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["truncated"] is True
        assert len(payload["matches"]) == 2  # noqa: PLR2004

    def test_search_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_search_files(MockSession(), ws, {"pattern": "*", "path": "."})


# =============================================================================
# handle_grep_files tests
# =============================================================================


class TestHandleGrepFiles:
    def test_grep_finds_matches(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file.py",)
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.return_value = "def foo():\n    pass\n"

        result = handle_grep_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "def foo", "path": "."},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert len(payload["matches"]) > 0
        assert payload["matches"][0]["text"] == "def foo():"

    def test_grep_literal_mode(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file.py",)
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.return_value = "def foo():\n    pass\n"

        result = handle_grep_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "def foo", "path": ".", "regex": False},
        )
        assert result.is_error is False

    def test_grep_case_insensitive(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file.py",)
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.return_value = "Def Foo():\n    pass\n"

        result = handle_grep_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "def foo", "path": ".", "case_sensitive": False},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert len(payload["matches"]) > 0

    def test_grep_whole_word(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file.py",)
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.return_value = "def foo():\n    pass\n"

        result = handle_grep_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "foo", "path": ".", "whole_word": True},
        )
        assert result.is_error is False

    def test_grep_with_context(self) -> None:
        ws = MagicMock()
        ws.iter_files.return_value = ("file.py",)
        ws.stat.return_value = {"type": "file", "size_bytes": 100}
        ws.read.return_value = "line0\nline1\nline2\nline3\n"

        result = handle_grep_files(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"pattern": "line2", "path": ".", "context_before": 1, "context_after": 1},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert len(payload["matches"]) > 0
        match = payload["matches"][0]
        assert "context_before" in match
        assert "context_after" in match

    def test_grep_invalid_regex_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_grep_files(
                MockSession(WORKSPACE_READ_CAPABILITY),
                ws,
                {"pattern": "[invalid", "path": "."},
            )

    def test_grep_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_grep_files(MockSession(), ws, {"pattern": "foo", "path": "."})


# =============================================================================
# handle_directory_tree tests
# =============================================================================


class TestHandleDirectoryTree:
    def test_returns_json_tree(self) -> None:
        ws = MagicMock()

        def list_dir_effect(p: str) -> list[str]:
            if p in (".", ""):
                return ["file.txt", "subdir"]
            return []

        # Handle both normalized ("") and non-normalized (".") path forms
        ws.is_dir.side_effect = lambda p: p in (".", "")
        ws.list_dir.side_effect = list_dir_effect
        ws.is_file.side_effect = lambda p: p == "file.txt"
        ws.exists.side_effect = lambda p: False

        result = handle_directory_tree(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "."},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["type"] == "dir"
        assert "children" in payload
        assert len(payload["children"]) == 2  # noqa: PLR2004

    def test_respects_max_depth(self) -> None:
        ws = MagicMock()
        ws.is_dir.side_effect = lambda p: p in (".", "")
        ws.list_dir.side_effect = lambda p: ["subdir"] if p in (".", "") else []
        ws.is_file.side_effect = lambda p: p == "subdir"
        ws.exists.side_effect = lambda p: False

        result = handle_directory_tree(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": ".", "max_depth": 1},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert len(payload["children"]) > 0
        for child in payload["children"]:
            if child["type"] == "dir":
                assert child["children"] == []

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_directory_tree(MockSession(), ws, {"path": "."})


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
        assert "new.txt" in cast("ToolContent", result.content[0]).text
        ws.write.assert_called_once()

    def test_writes_git_tracked_file_with_tracked_capability(self) -> None:
        ws = MagicMock()
        ws.exists.return_value = True
        ws.write.return_value = None

        session = MockSession(WORKSPACE_WRITE_TRACKED_CAPABILITY)
        result = handle_write_file(session, ws, {"path": "src/main.py", "content": "code"})
        assert result.is_error is False

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
        ws.exists.return_value = False  # file is untracked/ephemeral

        with pytest.raises(InvalidParamsError):
            handle_write_file(
                MockSession(WORKSPACE_WRITE_EPHEMERAL_CAPABILITY),
                ws,
                {"path": "file.txt"},
            )


# =============================================================================
# handle_edit_file tests
# =============================================================================


class TestHandleEditFile:
    def test_edits_file_successfully(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "hello world"
        ws.write.return_value = None

        result = handle_edit_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {"path": "file.txt", "edits": [{"oldText": "world", "newText": "there"}]},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["status"] == "applied"
        ws.write.assert_called_once()

    def test_dry_run_does_not_write(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "hello world"

        result = handle_edit_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {
                "path": "file.txt",
                "edits": [{"oldText": "world", "newText": "there"}],
                "dry_run": True,
            },
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["status"] == "preview"
        assert "diff" in payload
        ws.write.assert_not_called()

    def test_no_match_returns_error(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "hello world"

        result = handle_edit_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {
                "path": "file.txt",
                "edits": [{"oldText": "not found", "newText": "replacement"}],
            },
        )
        assert result.is_error is True
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["status"] == "no_match"

    def test_multi_edit_applies_in_order(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "a b c"
        ws.write.return_value = None

        result = handle_edit_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {
                "path": "file.txt",
                "edits": [
                    {"oldText": "a", "newText": "1"},
                    {"oldText": "b", "newText": "2"},
                ],
            },
        )
        assert result.is_error is False
        ws.write.assert_called_once()

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "content"

        with pytest.raises(CapabilityDeniedError):
            handle_edit_file(
                MockSession(),
                ws,
                {"path": "file.txt", "edits": [{"oldText": "content", "newText": "x"}]},
            )

    def test_empty_edits_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(InvalidParamsError):
            handle_edit_file(
                MockSession(WORKSPACE_EDIT_CAPABILITY), ws, {"path": "file.txt", "edits": []}
            )


# =============================================================================
# handle_append_file tests
# =============================================================================


class TestHandleAppendFile:
    def test_appends_to_file(self) -> None:
        ws = MagicMock()
        ws.append.return_value = None

        result = handle_append_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {"path": "file.txt", "content": "appended"},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["path"] == "file.txt"
        assert payload["bytes_appended"] == 8  # noqa: PLR2004

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_append_file(MockSession(), ws, {"path": "file.txt", "content": "test"})


# =============================================================================
# handle_create_directory tests
# =============================================================================


class TestHandleCreateDirectory:
    def test_creates_directory(self) -> None:
        ws = MagicMock()
        ws.mkdirs.return_value = None

        result = handle_create_directory(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {"path": "new/dir"},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["path"] == "new/dir"
        assert payload["created"] is True

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_create_directory(MockSession(), ws, {"path": "new/dir"})


# =============================================================================
# handle_move_file tests
# =============================================================================


class TestHandleMoveFile:
    def test_moves_file(self) -> None:
        ws = MagicMock()
        ws.move.return_value = None

        result = handle_move_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {"src": "old.txt", "dest": "new.txt"},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["src"] == "old.txt"
        assert payload["dest"] == "new.txt"

    def test_overwrite_true_succeeds(self) -> None:
        ws = MagicMock()
        ws.move.return_value = None

        result = handle_move_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {"src": "old.txt", "dest": "new.txt", "overwrite": True},
        )
        assert result.is_error is False

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_move_file(MockSession(), ws, {"src": "a.txt", "dest": "b.txt"})


# =============================================================================
# handle_copy_file tests
# =============================================================================


class TestHandleCopyFile:
    def test_copies_file(self) -> None:
        ws = MagicMock()
        ws.copy.return_value = None

        result = handle_copy_file(
            MockSession(WORKSPACE_EDIT_CAPABILITY),
            ws,
            {"src": "original.txt", "dest": "copy.txt"},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["src"] == "original.txt"
        assert payload["dest"] == "copy.txt"

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_copy_file(MockSession(), ws, {"src": "a.txt", "dest": "b.txt"})


# =============================================================================
# handle_delete_path tests
# =============================================================================


class TestHandleDeletePath:
    def test_deletes_file(self) -> None:
        ws = MagicMock()
        ws.delete.return_value = None

        result = handle_delete_path(
            MockSession(WORKSPACE_DELETE_CAPABILITY),
            ws,
            {"path": "file.txt"},
        )
        assert result.is_error is False
        payload = json.loads(cast("ToolContent", result.content[0]).text)
        assert payload["path"] == "file.txt"
        assert payload["deleted"] is True

    def test_deletes_directory_recursively(self) -> None:
        ws = MagicMock()
        ws.delete.return_value = None

        result = handle_delete_path(
            MockSession(WORKSPACE_DELETE_CAPABILITY),
            ws,
            {"path": "dir", "recursive": True},
        )
        assert result.is_error is False

    def test_refuses_directory_without_recursive(self) -> None:
        ws = MagicMock()
        ws.delete.side_effect = IsADirectoryError("Is a directory")

        result = handle_delete_path(
            MockSession(WORKSPACE_DELETE_CAPABILITY),
            ws,
            {"path": "dir"},
        )
        assert result.is_error is True

    def test_workspace_delete_distinct_from_edit(self) -> None:
        """WorkspaceDelete capability is distinct from WorkspaceEdit."""
        ws = MagicMock()
        ws.delete.return_value = None

        with pytest.raises(CapabilityDeniedError):
            handle_delete_path(
                MockSession(WORKSPACE_EDIT_CAPABILITY), ws, {"path": "file.txt"}
            )

    def test_missing_capability_raises(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError):
            handle_delete_path(MockSession(), ws, {"path": "file.txt"})


# =============================================================================
# _infer_image_mime_type tests
# =============================================================================


class TestInferImageMimeType:
    def test_png(self) -> None:
        assert _infer_image_mime_type("image.png") == "image/png"

    def test_jpg(self) -> None:
        assert _infer_image_mime_type("image.jpg") == "image/jpeg"

    def test_jpeg(self) -> None:
        assert _infer_image_mime_type("image.jpeg") == "image/jpeg"

    def test_gif(self) -> None:
        assert _infer_image_mime_type("image.gif") == "image/gif"

    def test_webp(self) -> None:
        assert _infer_image_mime_type("image.webp") == "image/webp"

    def test_unknown_suffix_returns_none(self) -> None:
        assert _infer_image_mime_type("document.pdf") is None
        assert _infer_image_mime_type("video.mp4") is None
        assert _infer_image_mime_type("unknown.xyz") is None

    def test_empty_suffix_returns_none(self) -> None:
        assert _infer_image_mime_type("noextension") is None


# =============================================================================
# handle_read_image tests
# =============================================================================


class TestHandleReadImage:
    def test_requires_media_read_capability(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError) as exc_info:
            handle_read_image(MockSession(), ws, {"path": "image.png"})

        assert "media.read" in str(exc_info.value)

    def test_returns_error_for_unsupported_format(self) -> None:
        ws = MagicMock()

        result = handle_read_image(
            MockSession(MEDIA_READ_CAPABILITY),
            ws,
            {"path": "document.pdf"},
        )

        assert result.is_error is True
        assert "Unsupported image format" in cast("ToolContent", result.content[0]).text
        assert ".pdf" in cast("ToolContent", result.content[0]).text

    def test_returns_error_for_missing_file(self) -> None:
        ws = MagicMock()
        ws.absolute_path.return_value = "/tmp/nonexistent.png"

        # MockSessionWithManifest is required for capability-aware delivery path
        result = handle_read_image(
            MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="claude"),
            ),
            ws,
            {"path": "nonexistent.png"},
        )
        assert result.is_error is True
        assert "Failed to read" in cast("ToolContent", result.content[0]).text

    def test_delivers_via_resource_reference_when_inline_too_large(self) -> None:
        """When inline image is too large, falls back to resource-reference delivery.

        This tests that handle_read_image (as a compatibility alias over
        _handle_workspace_media) properly routes oversized images through the
        resource-reference path when inline delivery is not possible.
        """
        ws = MagicMock()

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x00" * (DEFAULT_MAX_INLINE_BYTES + 1))
            temp_path = f.name

        try:
            ws.absolute_path.return_value = temp_path

            # MockSessionWithManifest with INLINE_IMAGE support but file exceeds limit
            result = handle_read_image(
                MockSessionWithManifest(
                    MEDIA_READ_CAPABILITY,
                    model_identity=MultimodalModelIdentity(provider="claude"),
                ),
                ws,
                {"path": "large.png"},
                max_inline_bytes=DEFAULT_MAX_INLINE_BYTES,
            )
            # With INLINE_IMAGE support but oversized file, falls back to resource-reference
            assert result.is_error is False
            content = result.content[0]
            # Should be a resource reference, not an inline image
            assert hasattr(content, "uri"), (
                f"Expected resource-reference block, got {type(content).__name__}"
            )
            assert content.uri.startswith("ralph://media/"), (
                f"Expected ralph://media/ URI, got: {content.uri}"
            )
        finally:
            Path(temp_path).unlink()

    def test_returns_image_content_block_on_success(self) -> None:
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path

            # MockSessionWithManifest with INLINE_IMAGE support (claude model)
            result = handle_read_image(
                MockSessionWithManifest(
                    MEDIA_READ_CAPABILITY,
                    model_identity=MultimodalModelIdentity(provider="claude"),
                ),
                ws,
                {"path": "test.png"},
            )
            assert result.is_error is False
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, ImageContent)
            assert content.type == "image"
            assert content.mime_type == "image/png"
        finally:
            Path(temp_path).unlink()

    def test_read_file_unchanged_text_only(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "hello world"

        result = handle_read_file(
            MockSession(WORKSPACE_READ_CAPABILITY),
            ws,
            {"path": "hello.txt"},
        )

        assert result.is_error is False
        assert hasattr(result.content[0], "text")
        assert cast("ToolContent", result.content[0]).text == "hello world"
        assert not isinstance(result.content[0], ImageContent)


def test_read_env_value_uses_injected_mapping() -> None:
    assert _read_env_value({"DEMO_KEY": "demo-value"}, "DEMO_KEY") == "demo-value"
    assert _read_env_value({}, "MISSING_KEY") == "[not found]"


# =============================================================================
# handle_read_media tests
# =============================================================================


@dataclass
class MockSessionWithManifest:
    allowed_capability: str | None = None
    session_id: str = "test-session"
    media_manifest: MediaManifest = field(default_factory=MediaManifest)
    model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)

    def check_capability(self, capability: str) -> object:
        return capability == self.allowed_capability

    def check_edit_area(self, path: str) -> object:
        return True


class TestHandleReadMedia:
    def test_no_manifest_returns_explicit_error(self) -> None:
        """When no session manifest is available, resource-reference delivery returns an error."""
        pdf_bytes = b"%PDF-1.4 fake pdf content"
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            # MockSession has no media_manifest attribute
            session = MockSession(MEDIA_READ_CAPABILITY)

            result = handle_read_media(session, ws, {"path": "report.pdf"})

            assert result.is_error is True
            msg = cast("ToolContent", result.content[0]).text
            assert "no active session manifest" in msg
            assert "report.pdf" in msg
        finally:
            Path(temp_path).unlink()

    def test_requires_media_read_capability(self) -> None:
        ws = MagicMock()

        with pytest.raises(CapabilityDeniedError) as exc_info:
            handle_read_media(MockSession(), ws, {"path": "image.png"})

        assert "media.read" in str(exc_info.value)

    def test_returns_error_for_unsupported_format(self) -> None:
        ws = MagicMock()

        result = handle_read_media(
            MockSession(MEDIA_READ_CAPABILITY),
            ws,
            {"path": "file.txt"},
        )

        assert result.is_error is True
        assert "Unsupported media format" in cast("ToolContent", result.content[0]).text

    def test_inline_image_returns_image_content_block(self) -> None:
        png_bytes = base64.b64decode(
            "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
            "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
        )

        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(png_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="claude"),
            )

            result = handle_read_media(session, ws, {"path": "test.png"})

            assert result.is_error is False
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, ImageContent)
            assert content.type == "image"
            assert content.mime_type == "image/png"
        finally:
            Path(temp_path).unlink()

    def test_pdf_returns_resource_reference_block(self) -> None:
        pdf_bytes = b"%PDF-1.4 fake pdf content"

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)

            result = handle_read_media(session, ws, {"path": "report.pdf"})

            assert result.is_error is False
            assert len(result.content) == 1
            content = result.content[0]
            assert isinstance(content, ResourceReferenceContent)
            assert content.type == "resource_reference"
            assert content.modality == "pdf"
            assert content.mime_type == "application/pdf"
            assert content.title == "report.pdf"
            assert content.delivery == "resource_reference_replay"
            assert content.uri.startswith("ralph://media/")
        finally:
            Path(temp_path).unlink()

    def test_pdf_stored_in_manifest(self) -> None:
        pdf_bytes = b"%PDF-1.4 fake pdf content"

        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)

            result = handle_read_media(session, ws, {"path": "report.pdf"})
            content = cast("ResourceReferenceContent", result.content[0])

            # The artifact must be stored in the manifest
            assert not session.media_manifest.is_empty()
            entries = session.media_manifest.list_entries()
            assert len(entries) == 1
            entry = entries[0]
            assert entry.uri == content.uri
            assert entry.mime_type == "application/pdf"
            assert entry.modality == "pdf"
            assert entry.raw_bytes == pdf_bytes
        finally:
            Path(temp_path).unlink()

    def test_audio_returns_resource_reference_block(self) -> None:
        mp3_bytes = b"ID3" + b"\x00" * 50  # Minimal fake MP3

        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(mp3_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)

            result = handle_read_media(session, ws, {"path": "clip.mp3"})

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, ResourceReferenceContent)
            assert content.modality == "audio"
            assert content.mime_type == "audio/mpeg"
            assert content.uri.startswith("ralph://media/")
        finally:
            Path(temp_path).unlink()

    def test_video_returns_resource_reference_block(self) -> None:
        mp4_bytes = b"\x00" * 100  # Fake video bytes

        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(mp4_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)

            result = handle_read_media(session, ws, {"path": "video.mp4"})

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, ResourceReferenceContent)
            assert content.modality == "video"
            assert content.mime_type == "video/mp4"
        finally:
            Path(temp_path).unlink()

    def test_oversized_image_returns_resource_reference_block(self) -> None:
        with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as f:
            f.write(b"\x89PNG" + b"\x00" * 100)  # Fake PNG
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)

            result = handle_read_media(
                session, ws, {"path": "large.png"}, max_inline_bytes=10
            )

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, ResourceReferenceContent)
            assert content.modality == "image"
            assert content.mime_type == "image/png"
        finally:
            Path(temp_path).unlink()

    def test_resource_reference_to_dict_shape(self) -> None:
        ref = ResourceReferenceContent(
            uri="ralph://media/test-id",
            mime_type="application/pdf",
            title="report.pdf",
            modality="pdf",
        )
        d = ref.to_dict()
        assert d["type"] == "resource_reference"
        assert d["uri"] == "ralph://media/test-id"
        assert d["mimeType"] == "application/pdf"
        assert d["title"] == "report.pdf"
        assert d["modality"] == "pdf"
        assert d["delivery"] == "resource_reference"


    def test_resource_reference_persists_to_session_index(self, tmp_path: Path) -> None:
        """handle_read_media must write artifact metadata to the session media index."""
        from ralph.workspace.fs import FsWorkspace  # noqa: PLC0415

        pdf_bytes = b"%PDF-1.4 fake pdf content"
        media_file = tmp_path / "report.pdf"
        media_file.write_bytes(pdf_bytes)

        @dataclass
        class SessionWithDrain:
            allowed_capability: str | None = None
            drain: str = "development"
            session_id: str = "test-session"
            media_manifest: MediaManifest = field(default_factory=MediaManifest)
            model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)

            def check_capability(self, capability: str) -> object:
                return capability == self.allowed_capability

            def check_edit_area(self, _: str) -> object:
                return True

        session = SessionWithDrain(MEDIA_READ_CAPABILITY)
        ws = FsWorkspace(tmp_path)

        result = handle_read_media(session, ws, {"path": "report.pdf"})

        assert result.is_error is False
        index_path = tmp_path / ".agent" / "tmp" / "development_media_session.json"
        assert index_path.exists(), (
            "Media session index must be written after resource_reference delivery"
        )
        data = json.loads(index_path.read_text(encoding="utf-8"))
        assert data["schema_version"] == "2"
        assert data["phase"] == "development"
        artifacts = data["artifacts"]
        assert len(artifacts) == 1
        assert artifacts[0]["modality"] == "pdf"
        assert artifacts[0]["mime_type"] == "application/pdf"
        assert artifacts[0]["title"] == "report.pdf"
        assert artifacts[0]["delivery"] == "resource_reference_replay"
        assert artifacts[0]["uri"].startswith("ralph://media/")
        assert artifacts[0]["source_path"] == "report.pdf"
        assert artifacts[0]["cache_path"].startswith(".agent/tmp/media/")
        assert artifacts[0]["source_uri"] == ""
        assert artifacts[0]["block_type"] == ""
        # Verify durable cache was written
        artifact_id = artifacts[0]["uri"].rsplit("/", 1)[-1]
        cache_file = tmp_path / ".agent" / "tmp" / "media" / artifact_id
        assert cache_file.exists(), "Durable cache file must be written alongside session index"
        assert cache_file.read_bytes() == pdf_bytes
        # Verify centralized registry entry
        registry_path = tmp_path / ".agent" / "tmp" / "media_registry.json"
        assert registry_path.exists(), "Centralized media registry must be written"
        registry = json.loads(registry_path.read_text(encoding="utf-8"))
        assert registry["schema_version"] == "2"
        reg_artifacts = registry["artifacts"]
        reg_entry = next(a for a in reg_artifacts if a["artifact_id"] == artifact_id)
        assert reg_entry["source_path"] == "report.pdf"
        assert reg_entry["cache_path"].startswith(".agent/tmp/media/")

    def test_resource_reference_accumulates_entries_in_session_index(self, tmp_path: Path) -> None:
        """Multiple read_media calls must append entries to the session index."""
        from ralph.workspace.fs import FsWorkspace  # noqa: PLC0415

        @dataclass
        class SessionWithDrain:
            allowed_capability: str | None = None
            drain: str = "development"
            session_id: str = "test-session"
            media_manifest: MediaManifest = field(default_factory=MediaManifest)
            model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)

            def check_capability(self, capability: str) -> object:
                return capability == self.allowed_capability

            def check_edit_area(self, _: str) -> object:
                return True

        session = SessionWithDrain(MEDIA_READ_CAPABILITY)
        ws = FsWorkspace(tmp_path)

        pdf1 = tmp_path / "a.pdf"
        pdf2 = tmp_path / "b.pdf"
        pdf1.write_bytes(b"%PDF-1.4 doc1")
        pdf2.write_bytes(b"%PDF-1.4 doc2")

        handle_read_media(session, ws, {"path": "a.pdf"})
        handle_read_media(session, ws, {"path": "b.pdf"})

        index_path = tmp_path / ".agent" / "tmp" / "development_media_session.json"
        data = json.loads(index_path.read_text(encoding="utf-8"))
        artifacts = data["artifacts"]
        assert len(artifacts) == 2  # noqa: PLR2004
        titles = {a["title"] for a in artifacts}
        assert "a.pdf" in titles
        assert "b.pdf" in titles

    # -------------------------------------------------------------------------
    # Typed-block delivery tests (Claude provider)
    # -------------------------------------------------------------------------

    def test_claude_pdf_returns_typed_pdf_block(self) -> None:
        from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity  # noqa: PLC0415

        pdf_bytes = b"%PDF-1.4 fake pdf content"
        with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as f:
            f.write(pdf_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(
                    provider="claude", model_id="claude-3-5-sonnet-20241022"
                ),
            )
            result = handle_read_media(session, ws, {"path": "report.pdf"})

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, PdfContent)
            assert content.type == "pdf"
            assert content.delivery == "typed_block"
            assert content.uri.startswith("ralph://media/")
            assert content.mime_type == "application/pdf"
            assert content.title == "report.pdf"
        finally:
            Path(temp_path).unlink()

    def test_gemini_audio_returns_typed_audio_block(self) -> None:
        from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity  # noqa: PLC0415

        mp3_bytes = b"ID3" + b"\x00" * 50
        with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
            f.write(mp3_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="gemini"),
            )
            result = handle_read_media(session, ws, {"path": "clip.mp3"})

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, AudioContent)
            assert content.type == "audio"
            assert content.delivery == "typed_block"
        finally:
            Path(temp_path).unlink()

    def test_gemini_video_returns_typed_video_block(self) -> None:
        from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity  # noqa: PLC0415

        mp4_bytes = b"\x00" * 100
        with tempfile.NamedTemporaryFile(suffix=".mp4", delete=False) as f:
            f.write(mp4_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="gemini"),
            )
            result = handle_read_media(session, ws, {"path": "video.mp4"})

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, VideoContent)
            assert content.type == "video"
            assert content.delivery == "typed_block"
        finally:
            Path(temp_path).unlink()

    def test_claude_document_returns_typed_document_block(self) -> None:
        from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity  # noqa: PLC0415

        docx_bytes = b"PK\x03\x04" + b"\x00" * 50  # Fake DOCX (zip magic)
        with tempfile.NamedTemporaryFile(suffix=".docx", delete=False) as f:
            f.write(docx_bytes)
            temp_path = f.name

        try:
            ws = MagicMock()
            ws.absolute_path.return_value = temp_path
            session = MockSessionWithManifest(
                MEDIA_READ_CAPABILITY,
                model_identity=MultimodalModelIdentity(provider="claude"),
            )
            result = handle_read_media(session, ws, {"path": "doc.docx"})

            assert result.is_error is False
            content = result.content[0]
            assert isinstance(content, DocumentContent)
            assert content.type == "document"
            assert content.delivery == "typed_block"
        finally:
            Path(temp_path).unlink()

    # -------------------------------------------------------------------------
    # Replay handle tests (ralph://media/{artifact_id})
    # -------------------------------------------------------------------------

    def test_replay_handle_precedence_before_filesystem_lookup(self) -> None:
        """A ralph://media/... handle must be resolved from manifest before any filesystem check."""
        from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity  # noqa: PLC0415

        png_bytes = b"\x89PNG" + b"\x00" * 20
        session = MockSessionWithManifest(
            MEDIA_READ_CAPABILITY,
            model_identity=MultimodalModelIdentity(provider="claude"),
        )
        entry = session.media_manifest.add(
            title="capture.png",
            mime_type="image/png",
            modality="image",
            raw_bytes=png_bytes,
        )

        ws = MagicMock()
        result = handle_read_media(session, ws, {"path": entry.uri})

        # Should succeed from manifest without touching the filesystem
        assert result.is_error is False
        ws.absolute_path.assert_not_called()
        content = result.content[0]
        assert isinstance(content, ImageContent)

    def test_replay_handle_returns_typed_pdf_block_from_manifest(self) -> None:
        from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity  # noqa: PLC0415

        pdf_bytes = b"%PDF-1.4 fake"
        session = MockSessionWithManifest(
            MEDIA_READ_CAPABILITY,
            model_identity=MultimodalModelIdentity(provider="claude"),
        )
        entry = session.media_manifest.add(
            title="report.pdf",
            mime_type="application/pdf",
            modality="pdf",
            raw_bytes=pdf_bytes,
        )

        ws = MagicMock()
        result = handle_read_media(session, ws, {"path": entry.uri})

        assert result.is_error is False
        content = result.content[0]
        assert isinstance(content, PdfContent)
        assert content.uri == entry.uri

    def test_replay_invalid_handle_returns_invalid_replay_handle_error(self) -> None:
        session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)
        ws = MagicMock()

        result = handle_read_media(session, ws, {"path": "ralph://media/not-a-valid-uuid"})

        assert result.is_error is True
        text = cast("ToolContent", result.content[0]).text
        assert MultimodalFailureKind.INVALID_REPLAY_HANDLE in text

    def test_replay_unknown_artifact_id_returns_missing_replay_source_error(self) -> None:
        import uuid  # noqa: PLC0415

        from ralph.mcp.multimodal.resources import build_media_uri  # noqa: PLC0415

        unknown_uri = build_media_uri(str(uuid.uuid4()))
        session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)
        ws = MagicMock()

        result = handle_read_media(session, ws, {"path": unknown_uri})

        assert result.is_error is True
        text = cast("ToolContent", result.content[0]).text
        assert MultimodalFailureKind.MISSING_REPLAY_SOURCE in text

    def test_cross_session_replay_from_persisted_cache(self, tmp_path: Path) -> None:
        """A replay handle must work across sessions using the persisted registry."""
        from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity  # noqa: PLC0415
        from ralph.workspace.fs import FsWorkspace  # noqa: PLC0415

        @dataclass
        class SessionWithDrain:
            allowed_capability: str | None = None
            drain: str = "development"
            session_id: str = "test-session"
            media_manifest: MediaManifest = field(default_factory=MediaManifest)
            model_identity: MultimodalModelIdentity = field(
                default_factory=lambda: MultimodalModelIdentity(provider="claude")
            )

            def check_capability(self, capability: str) -> object:
                return capability == self.allowed_capability

            def check_edit_area(self, _: str) -> object:
                return True

        pdf_bytes = b"%PDF-1.4 fake pdf for cross-session"
        media_file = tmp_path / "doc.pdf"
        media_file.write_bytes(pdf_bytes)

        # Session 1: read the file and persist to registry
        session1 = SessionWithDrain(MEDIA_READ_CAPABILITY)
        ws = FsWorkspace(tmp_path)
        result1 = handle_read_media(session1, ws, {"path": "doc.pdf"})
        assert result1.is_error is False
        # The artifact URI from session 1
        from ralph.mcp.multimodal.artifacts import PdfContent as PdfContentClass  # noqa: PLC0415
        content1 = result1.content[0]
        assert isinstance(content1, PdfContentClass)
        artifact_uri = content1.uri

        # Session 2: new empty manifest (simulates new session)
        session2 = SessionWithDrain(MEDIA_READ_CAPABILITY)
        assert session2.media_manifest.get(artifact_uri.rsplit("/", 1)[-1]) is None

        # Replay from persisted registry
        result2 = handle_read_media(session2, ws, {"path": artifact_uri})
        assert result2.is_error is False
        content2 = result2.content[0]
        assert isinstance(content2, PdfContentClass)
        assert content2.uri == artifact_uri

    def test_cross_session_replay_fails_when_cache_deleted(self, tmp_path: Path) -> None:
        """Replay returns missing_replay_source when cache bytes are gone."""
        from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity  # noqa: PLC0415
        from ralph.workspace.fs import FsWorkspace  # noqa: PLC0415

        @dataclass
        class SessionWithDrain:
            allowed_capability: str | None = None
            drain: str = "development"
            session_id: str = "test-session"
            media_manifest: MediaManifest = field(default_factory=MediaManifest)
            model_identity: MultimodalModelIdentity = field(
                default_factory=lambda: MultimodalModelIdentity(provider="claude")
            )

            def check_capability(self, capability: str) -> object:
                return capability == self.allowed_capability

            def check_edit_area(self, _: str) -> object:
                return True

        pdf_bytes = b"%PDF-1.4 fake pdf for cache-deleted test"
        media_file = tmp_path / "gone.pdf"
        media_file.write_bytes(pdf_bytes)

        session1 = SessionWithDrain(MEDIA_READ_CAPABILITY)
        ws = FsWorkspace(tmp_path)
        result1 = handle_read_media(session1, ws, {"path": "gone.pdf"})
        assert result1.is_error is False
        from ralph.mcp.multimodal.artifacts import PdfContent as PdfContentClass2  # noqa: PLC0415
        content1 = result1.content[0]
        assert isinstance(content1, PdfContentClass2)
        artifact_uri = content1.uri
        artifact_id = artifact_uri.rsplit("/", 1)[-1]

        # Delete both the durable cache and the source file
        cache_file = tmp_path / ".agent" / "tmp" / "media" / artifact_id
        cache_file.unlink()
        media_file.unlink()

        # Session 2: replay should fail with missing_replay_source
        session2 = SessionWithDrain(MEDIA_READ_CAPABILITY)
        result2 = handle_read_media(session2, ws, {"path": artifact_uri})
        assert result2.is_error is True
        text = cast("ToolContent", result2.content[0]).text
        assert MultimodalFailureKind.MISSING_REPLAY_SOURCE in text

    def test_typed_block_to_dict_shapes(self) -> None:
        pdf = PdfContent(uri="ralph://media/x", mime_type="application/pdf", title="r.pdf")
        doc = DocumentContent(
            uri="ralph://media/y",
            mime_type="application/vnd.openxmlformats-officedocument.wordprocessingml.document",
            title="d.docx",
        )
        audio = AudioContent(uri="ralph://media/a", mime_type="audio/mpeg", title="c.mp3")
        video = VideoContent(uri="ralph://media/v", mime_type="video/mp4", title="v.mp4")

        assert pdf.to_dict()["type"] == "pdf"
        assert pdf.to_dict()["mimeType"] == "application/pdf"
        assert pdf.to_dict()["delivery"] == "typed_block"
        assert doc.to_dict()["type"] == "document"
        assert audio.to_dict()["type"] == "audio"
        assert video.to_dict()["type"] == "video"


# ---------------------------------------------------------------------------
# Named acceptance tests required by the managed-runtime plan (Step 3)
# ---------------------------------------------------------------------------


def test_read_media_replay_handle_round_trips_audio_metadata() -> None:
    """Replaying a ralph://media/ handle for audio preserves URI, modality, and mime_type.

    After read_media stores audio in the session manifest, replaying the handle
    must return a resource_reference block with the original metadata intact.
    """
    mp3_bytes = b"ID3" + b"\x00" * 50

    session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)
    entry = session.media_manifest.add(
        title="clip.mp3",
        mime_type="audio/mpeg",
        modality="audio",
        raw_bytes=mp3_bytes,
    )

    ws = MagicMock()
    result = handle_read_media(session, ws, {"path": entry.uri})

    assert result.is_error is False, f"Expected success, got error: {result.content}"
    block = result.content[0]
    # For unknown provider, audio → resource_reference_replay
    assert isinstance(block, ResourceReferenceContent), (
        f"Expected ResourceReferenceContent for audio replay, got {type(block).__name__}"
    )
    assert block.uri == entry.uri, f"URI mismatch: {block.uri!r} != {entry.uri!r}"
    assert block.modality == "audio", f"Modality mismatch: {block.modality!r}"
    assert block.mime_type == "audio/mpeg", f"MIME type mismatch: {block.mime_type!r}"
    # Filesystem must not be touched — the manifest was the source
    ws.absolute_path.assert_not_called()


def test_read_media_replay_handle_round_trips_video_metadata() -> None:
    """Replaying a ralph://media/ handle for video preserves URI, modality, and mime_type."""
    mp4_bytes = b"\x00\x00\x00\x20ftyp" + b"\x00" * 40

    session = MockSessionWithManifest(MEDIA_READ_CAPABILITY)
    entry = session.media_manifest.add(
        title="video.mp4",
        mime_type="video/mp4",
        modality="video",
        raw_bytes=mp4_bytes,
    )

    ws = MagicMock()
    result = handle_read_media(session, ws, {"path": entry.uri})

    assert result.is_error is False, f"Expected success, got error: {result.content}"
    block = result.content[0]
    assert isinstance(block, ResourceReferenceContent), (
        f"Expected ResourceReferenceContent for video replay, got {type(block).__name__}"
    )
    assert block.uri == entry.uri, f"URI mismatch: {block.uri!r} != {entry.uri!r}"
    assert block.modality == "video", f"Modality mismatch: {block.modality!r}"
    assert block.mime_type == "video/mp4", f"MIME type mismatch: {block.mime_type!r}"
    ws.absolute_path.assert_not_called()


def test_persist_upstream_media_artifacts_writes_session_index(tmp_path: Path) -> None:
    """persist_upstream_media_artifacts must write embedded upstream blocks to the session index.

    When an upstream tool returns embedded bytes (stored as ralph://media/... in the manifest),
    the caller should call persist_upstream_media_artifacts so the artifact survives the session
    in the session index and registry for cross-session replay.
    """
    from ralph.mcp.multimodal.resources import MediaManifest  # noqa: PLC0415
    from ralph.mcp.tools.workspace import persist_upstream_media_artifacts  # noqa: PLC0415
    from ralph.workspace.fs import FsWorkspace  # noqa: PLC0415

    manifest = MediaManifest()
    audio_entry = manifest.add(
        title="clip.mp3",
        mime_type="audio/mpeg",
        modality="audio",
        raw_bytes=b"ID3" + b"\x00" * 50,
    )

    result: dict[str, object] = {
        "content": [
            {"type": "text", "text": "header"},
            {
                "type": "resource_reference",
                "uri": audio_entry.uri,
                "mimeType": "audio/mpeg",
                "title": "clip.mp3",
                "modality": "audio",
                "delivery": "resource_reference_replay",
            },
        ]
    }

    @dataclass
    class _Session:
        drain: str = "development"
        session_id: str = "test"
        allowed_capability: str | None = "media.read"
        media_manifest: MediaManifest = field(default_factory=MediaManifest)
        model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)

        def check_capability(self, cap: str) -> object:
            return cap == self.allowed_capability

    session = _Session(media_manifest=manifest)
    ws = FsWorkspace(tmp_path)

    persist_upstream_media_artifacts(result, session, ws)

    index_path = tmp_path / ".agent" / "tmp" / "development_media_session.json"
    assert index_path.exists(), "Session index must be written for embedded upstream blocks"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    artifacts = data["artifacts"]
    assert len(artifacts) == 1
    a = artifacts[0]
    assert a["modality"] == "audio"
    assert a["delivery"] == "resource_reference_replay"
    assert a["uri"] == audio_entry.uri
    assert a["mime_type"] == "audio/mpeg"
    assert a["title"] == "clip.mp3"
    assert a["cache_path"].startswith(".agent/tmp/media/")
    # Durable cache file must be written
    artifact_id = audio_entry.uri.rsplit("/", 1)[-1]
    cache_file = tmp_path / ".agent" / "tmp" / "media" / artifact_id
    assert cache_file.exists(), "Durable cache file must be written for embedded upstream blocks"
    assert cache_file.read_bytes() == b"ID3" + b"\x00" * 50


def test_persist_upstream_media_artifacts_writes_uri_backed_as_unsupported_seam(
    tmp_path: Path,
) -> None:
    """URI-backed blocks must be written as unsupported_runtime_seam entries.

    URI-backed blocks (delivery='resource_reference') reference external URIs and
    cannot be replayed across sessions. They must be written to the session index
    as unsupported_runtime_seam entries so the failure is explicit at invoke time.
    """
    import json  # noqa: PLC0415

    from ralph.mcp.tools.workspace import persist_upstream_media_artifacts  # noqa: PLC0415
    from ralph.workspace.fs import FsWorkspace  # noqa: PLC0415

    result: dict[str, object] = {
        "content": [
            {
                "type": "resource_reference",
                "uri": "https://example.com/report.pdf",
                "mimeType": "application/pdf",
                "title": "report.pdf",
                "modality": "pdf",
                "delivery": "resource_reference",
            },
        ]
    }

    @dataclass
    class _Session:
        drain: str = "development"
        session_id: str = "test"
        media_manifest: MediaManifest = field(default_factory=MediaManifest)
        model_identity: MultimodalModelIdentity = field(default=UNKNOWN_IDENTITY)

        def check_capability(self, cap: str) -> object:
            return True

    session = _Session()
    ws = FsWorkspace(tmp_path)

    persist_upstream_media_artifacts(result, session, ws)

    index_path = tmp_path / ".agent" / "tmp" / "development_media_session.json"
    assert index_path.exists(), (
        "URI-backed resource_reference blocks must be written as unsupported_runtime_seam entries"
    )
    data = json.loads(index_path.read_text(encoding="utf-8"))
    artifacts = data["artifacts"]
    assert len(artifacts) == 1
    entry = artifacts[0]
    assert entry["delivery"] == "unsupported"
    assert entry["failure_kind"] == "unsupported_runtime_seam"
    assert entry["source_uri"] == "https://example.com/report.pdf"
    assert entry["modality"] == "pdf"
    assert entry["title"] == "report.pdf"
    assert entry["cache_path"] == ""  # No cache path for URI-backed artifacts


def test_unknown_provider_media_preserves_delivery_metadata() -> None:
    """For an unknown provider, resource_reference blocks carry modality and URI metadata.

    Unknown providers default to resource_reference_replay, which must preserve
    enough metadata for agents to diagnose the delivery mode without re-reading
    the raw artifact.
    """
    import tempfile  # noqa: PLC0415

    mp3_bytes = b"ID3" + b"\x00" * 50

    with tempfile.NamedTemporaryFile(suffix=".mp3", delete=False) as f:
        f.write(mp3_bytes)
        temp_path = f.name

    try:
        ws = MagicMock()
        ws.absolute_path.return_value = temp_path
        # UNKNOWN_IDENTITY → resource_reference_replay for all modalities
        session = MockSessionWithManifest(MEDIA_READ_CAPABILITY, model_identity=UNKNOWN_IDENTITY)

        result = handle_read_media(session, ws, {"path": "clip.mp3"})

        assert result.is_error is False, f"Expected success, got error: {result.content}"
        block = result.content[0]
        assert isinstance(block, ResourceReferenceContent), (
            f"Expected ResourceReferenceContent, got {type(block).__name__}"
        )
        assert block.modality == "audio", f"Modality must be 'audio', got: {block.modality!r}"
        assert block.uri.startswith("ralph://media/"), (
            f"URI must be a ralph://media/ handle: {block.uri!r}"
        )
        assert block.mime_type == "audio/mpeg", (
            f"MIME type must be preserved: {block.mime_type!r}"
        )
    finally:
        Path(temp_path).unlink()


def test_persist_media_session_entry_stores_failure_kind(tmp_path: Path) -> None:
    """Session index entries written by read_media must include the failure_kind field.

    This proves the canonical artifact schema carries failure_kind so that
    unsupported_runtime_seam remains distinct from unsupported_modality through
    sidecar persistence and invoke-time rendering.
    """
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity  # noqa: PLC0415
    from ralph.workspace.fs import FsWorkspace  # noqa: PLC0415

    @dataclass
    class _Session:
        allowed_capability: str | None = None
        drain: str = "development"
        session_id: str = "test-session"
        media_manifest: MediaManifest = field(default_factory=MediaManifest)
        model_identity: MultimodalModelIdentity = field(
            default_factory=lambda: MultimodalModelIdentity(provider="unknown-x")
        )

        def check_capability(self, capability: str) -> object:
            return capability == self.allowed_capability

        def check_edit_area(self, _: str) -> object:
            return True

    pdf_file = tmp_path / "report.pdf"
    pdf_file.write_bytes(b"%PDF-1.4 test")

    session = _Session(MEDIA_READ_CAPABILITY)
    ws = FsWorkspace(tmp_path)
    result = handle_read_media(session, ws, {"path": "report.pdf"})

    assert result.is_error is False, f"Expected success, got error: {result.content}"

    index_path = tmp_path / ".agent" / "tmp" / "development_media_session.json"
    assert index_path.exists(), "Session index must be written after read_media"
    data = json.loads(index_path.read_text(encoding="utf-8"))
    artifacts = data["artifacts"]
    assert len(artifacts) == 1
    entry = artifacts[0]
    assert "failure_kind" in entry, (
        "Persisted artifact entry must include 'failure_kind' field in canonical schema"
    )
    assert entry["failure_kind"] == "", (
        "Successful delivery must store empty failure_kind, not a failure classification"
    )

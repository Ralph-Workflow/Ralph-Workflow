"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations
from tests.mock_session import MockSession

import json
from typing import cast
from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.coordination import (
    InvalidParamsError,
    ToolContent,
)
from ralph.mcp.tools.workspace import (
    WORKSPACE_READ_CAPABILITY,
    handle_read_file,
)

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


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
        assert payload["returned_lines"] == 2
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
        assert payload["total_bytes"] == 200
        assert payload["returned_bytes"] == 100
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
        assert payload["total_bytes"] == 100
        ws.read_bytes.assert_called_once()
        _, kwargs = ws.read_bytes.call_args
        assert kwargs["offset"] == 17
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
        assert kwargs["start"] == 2
        assert kwargs["end"] == 3

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
        assert kwargs["head"] == 2

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
        assert kwargs["tail"] == 2

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
        assert kwargs["limit"] == 100


"""Tests for ralph/mcp/tool_workspace.py — MCP workspace tool handlers."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    ToolError,
)
from ralph.mcp.tools.workspace import (
    WORKSPACE_READ_CAPABILITY,
    handle_read_file,
)
from ralph.workspace import MemoryWorkspace, WorkspaceSnapshot
from tests.mock_session import MockSession

MEDIA_READ_CAPABILITY = "media.read"
DEFAULT_MAX_INLINE_BYTES = 5_242_880


class TestHandleReadFile:
    def test_reads_file_content(self) -> None:
        ws = MagicMock()
        ws.read.return_value = "file contents"

        result = handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "file.txt"})
        assert "file contents" in result.content[0].text
        assert result.is_error is False

    def test_full_read_reuses_one_snapshot_for_metadata_and_content(self) -> None:
        class RecordingWorkspace(MemoryWorkspace):
            def __init__(self) -> None:
                super().__init__()
                self.snapshot_count = 0
                self.read_count = 0
                self.stat_count = 0

            def snapshot(
                self, path: str, *, max_bytes: int | None = None
            ) -> WorkspaceSnapshot:
                self.snapshot_count += 1
                return super().snapshot(path, max_bytes=max_bytes)

            def read(self, path: str) -> str:
                self.read_count += 1
                return super().read(path)

            def stat(self, path: str) -> dict[str, object]:
                self.stat_count += 1
                return super().stat(path)

        ws = RecordingWorkspace()
        ws.write("file.txt", "file contents")

        result = handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "file.txt"})

        assert result.is_error is False
        assert "file contents" in result.content[0].text
        assert ws.snapshot_count == 1
        assert ws.read_count == 0
        assert ws.stat_count == 0

    def test_snapshot_preserves_directory_type_when_memory_workspace_has_a_name_collision(self) -> None:
        """S-4: one observation preserves the workspace's public directory semantics."""
        ws = MemoryWorkspace()
        ws.create_dir("occupied")
        ws.write("occupied", "not readable as a file")

        snapshot = ws.snapshot("occupied")

        assert snapshot.stat["type"] == "dir"
        assert snapshot.content is None

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

    def test_rejected_path_raises_tool_error_not_a_retryable_protocol_error(self) -> None:
        """Path resolution raises ``ValueError`` for a path carrying an embedded
        NUL or resolving outside the workspace. Uncaught, that leaves the handler
        as a retryable protocol error the agent re-issues; ``write_file``,
        ``delete_path`` and ``stat_path`` all degrade to a terminal error here."""
        ws = MagicMock()
        ws.snapshot = None
        ws.stat.side_effect = ValueError("embedded null byte")

        with pytest.raises(ToolError):
            handle_read_file(MockSession(WORKSPACE_READ_CAPABILITY), ws, {"path": "a\x00b.txt"})

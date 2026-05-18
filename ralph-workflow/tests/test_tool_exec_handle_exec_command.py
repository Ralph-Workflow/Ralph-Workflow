"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations
from tests.mock_workspace_root import MockWorkspaceRoot
from tests.mock_session import MockSession

from typing import TYPE_CHECKING

import pytest

from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    ToolContent,
)
from ralph.mcp.tools.exec import (
    handle_exec_command,
)

if TYPE_CHECKING:
    from pathlib import Path

CUSTOM_TIMEOUT_MS = 5000
EXPECTED_TIMEOUT_SECONDS = 2.5


class TestHandleExecCommand:
    def test_exec_with_valid_command_succeeds(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "echo", "args": ["hello"], "timeout_ms": 5000}

        result = handle_exec_command(session, workspace, params)
        assert result.is_error is False
        content = result.content[0]
        assert isinstance(content, ToolContent)
        assert "hello" in content.text

    def test_exec_without_capability_raises(self, tmp_path: Path) -> None:
        session = MockSession(set())  # No capabilities
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "ls", "args": []}

        with pytest.raises(CapabilityDeniedError):
            handle_exec_command(session, workspace, params)

    def test_exec_with_blacklisted_command_raises(self, tmp_path: Path) -> None:
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "git", "args": ["status"]}

        with pytest.raises(CapabilityDeniedError):
            handle_exec_command(session, workspace, params)

    def test_exec_returns_error_on_nonzero_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "false", "args": [], "timeout_ms": 5000}

        result = handle_exec_command(session, workspace, params)
        assert result.is_error is True



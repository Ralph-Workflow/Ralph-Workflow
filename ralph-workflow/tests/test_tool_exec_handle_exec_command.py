"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

import subprocess
from typing import TYPE_CHECKING

import pytest

import ralph.mcp.tools._exec_completed_process as exec_completed_process
import ralph.mcp.tools.exec as exec_tool
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    ToolContent,
)
from ralph.mcp.tools.exec import (
    DEFAULT_TIMEOUT_MS,
    ExecRunDeps,
    handle_exec_command,
    parse_exec_params,
)
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

if TYPE_CHECKING:
    from pathlib import Path


class _StreamingMockSession(MockSession):
    """MockSession that records stream_tool_output calls."""

    def __init__(self, caps: set[str]) -> None:
        super().__init__(caps)
        self.streamed_events: list[dict[str, object]] = []

    def stream_tool_output(self, event: dict[str, object]) -> None:
        self.streamed_events.append(event)

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

    def test_exec_allows_git_command(self, tmp_path: Path) -> None:
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "git", "args": ["--version"]}

        result = handle_exec_command(session, workspace, params)
        assert result.is_error is False
        content = result.content[0]
        assert isinstance(content, ToolContent)
        assert "git version" in content.text.lower()

    def test_exec_with_blacklisted_command_raises(self, tmp_path: Path) -> None:
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "sudo", "args": ["ls"]}

        with pytest.raises(CapabilityDeniedError):
            handle_exec_command(session, workspace, params)

    def test_exec_timeout_returns_actionable_is_error_result_not_exception(
        self, tmp_path: Path
    ) -> None:
        """A timed-out exec must come back as an is_error ToolResult (not a raised
        ExecutionError that the MCP server turns into a -32603 protocol error the
        agent retries forever). The message must be actionable and non-retryable."""

        def _timeout_runner(
            command: list[str], cwd: object, timeout_seconds: float | None
        ) -> exec_completed_process._CompletedProcessAdapter:
            del cwd
            raise subprocess.TimeoutExpired(cmd=command, timeout=timeout_seconds or 30.0)

        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "echo", "args": [], "timeout_ms": 5000}

        result = handle_exec_command(
            session, workspace, params, deps=ExecRunDeps(runner=_timeout_runner)
        )

        assert result.is_error is True
        content = result.content[0]
        assert isinstance(content, ToolContent)
        text = content.text.lower()
        assert "timed out" in text
        assert "timeout_ms" in text
        assert "do not retry" in text

    def test_exec_default_timeout_exceeds_verify_budget(self) -> None:
        """The exec default must exceed the 60s combined verify budget so an agent
        running verification through exec does not time out on every call."""
        assert DEFAULT_TIMEOUT_MS > 60_000
        assert parse_exec_params({"command": "make"}).timeout_ms > 60_000

    def test_exec_returns_error_on_nonzero_exit(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "false", "args": [], "timeout_ms": 5000}

        monkeypatch.setattr(
            exec_tool,
            "run_command",
            lambda *args, **kwargs: exec_completed_process._CompletedProcessAdapter(
                stdout=b"", stderr=b"", returncode=1
            ),
        )

        result = handle_exec_command(session, workspace, params)
        assert result.is_error is True


    def test_session_stream_tool_output_receives_chunks_before_final_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """handle_exec_command forwards on_output_chunk events via session.stream_tool_output."""

        def fake_run_command(
            command: str,
            args: list[str],
            workspace: object,
            timeout_ms: int,
            deps: ExecRunDeps | None = None,
        ) -> exec_completed_process._CompletedProcessAdapter:
            del command, args, workspace, timeout_ms
            if deps is not None and deps.on_output_chunk is not None:
                deps.on_output_chunk("streamed-chunk")
            return exec_completed_process._CompletedProcessAdapter(
                stdout=b"streamed-chunk", stderr=b"", returncode=0
            )

        monkeypatch.setattr(exec_tool, "run_command", fake_run_command)

        session = _StreamingMockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "echo", "args": [], "timeout_ms": 5000}

        result = handle_exec_command(session, workspace, params)

        assert result.is_error is False
        streamed = session.streamed_events
        assert streamed, "stream_tool_output must be called at least once"
        for event in streamed:
            assert event.get("tool") == "exec"
            assert event.get("stream") == "combined"
            assert isinstance(event.get("text"), str)
        text_parts: list[str] = []
        for event in streamed:
            text_val = event.get("text")
            if isinstance(text_val, str):
                text_parts.append(text_val)
        assert "streamed-chunk" in "".join(text_parts)

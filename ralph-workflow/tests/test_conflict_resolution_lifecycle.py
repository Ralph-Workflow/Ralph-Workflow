"""Lifecycle ordering contracts for conflict-resolution process quiescence (S-6)."""

from __future__ import annotations

from dataclasses import dataclass

from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess


@dataclass
class _TrackedProcess:
    terminal: bool = False
    terminated: int = 0

    @property
    def pid(self) -> int:
        return 42

    def poll(self) -> int | None:
        return 0 if self.terminal else None

    def terminate(self, grace_period_s: float = 5.0) -> None:
        del grace_period_s
        self.terminated += 1
        self.terminal = True

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.terminal = True
        return 0

    def kill(self) -> None:
        self.terminal = True


def test_conflict_resolution_lifecycle_shutdown_reaps_server_before_next_action(tmp_path) -> None:
    """S-6/R9: bridge shutdown reaches a terminal server before the next git action."""
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    process = _TrackedProcess()
    bridge = StandaloneMcpProcess("http://127.0.0.1:1/mcp", process, session_file)

    bridge.shutdown()

    assert process.terminal is True
    assert process.terminated == 1
    assert session_file.exists() is False


@dataclass
class _UnreapedProcess:
    """A process double whose terminate call never establishes terminal state."""

    terminated: int = 0
    returncode: int | None = None

    @property
    def pid(self) -> int:
        return 43

    def poll(self) -> int | None:
        return None

    def terminate(self, grace_period_s: float = 5.0) -> None:
        del grace_period_s
        self.terminated += 1

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        return self.returncode

    def kill(self) -> None:
        return None


def test_conflict_resolution_lifecycle_keeps_session_artifact_until_server_is_terminal(tmp_path) -> None:
    """S-5/R9: recovery cannot release an MCP session file after an unproven reap."""
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    process = _UnreapedProcess()
    server = StandaloneMcpProcess("http://127.0.0.1:1/mcp", process, session_file)

    try:
        server.shutdown()
    except McpServerError as exc:
        assert "did not reach terminal state" in str(exc)
    else:
        raise AssertionError("an unreaped MCP server must block recovery")

    assert process.terminated == 1
    assert session_file.exists() is True


@dataclass
class _UnreapableProcess(_TrackedProcess):
    """A process double that reports a bounded reap failure."""

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        raise TimeoutError("still running")


def test_conflict_resolution_lifecycle_keeps_session_artifact_when_reap_times_out(tmp_path) -> None:
    """S-5/R9: a bounded reap error blocks recovery and retains server authority."""
    session_file = tmp_path / "timeout-session.json"
    session_file.write_text("{}", encoding="utf-8")
    process = _UnreapableProcess()
    server = StandaloneMcpProcess("http://127.0.0.1:1/mcp", process, session_file)

    try:
        server.shutdown()
    except McpServerError as exc:
        assert "could not be reaped" in str(exc)
    else:
        raise AssertionError("a reap timeout must block recovery")

    assert process.terminated == 1
    assert session_file.exists() is True

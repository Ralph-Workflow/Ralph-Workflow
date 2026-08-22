"""Lifecycle ordering contracts for conflict-resolution process quiescence (S-6)."""

from __future__ import annotations

from dataclasses import dataclass

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

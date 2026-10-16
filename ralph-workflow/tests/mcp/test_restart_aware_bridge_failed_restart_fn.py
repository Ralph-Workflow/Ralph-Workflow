"""S1 containment regression: counter increments every attempt."""

from __future__ import annotations

import pathlib
import tempfile

import pytest

from ralph.mcp.server import lifecycle

pytestmark = pytest.mark.subprocess_e2e


class _FakeProcess:
    def __init__(self, poll_result: int | None, pid: int = 9999) -> None:
        self._poll_result = poll_result
        self._pid = pid

    @property
    def pid(self) -> int:
        return self._pid

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
        return None


def _make_inner(poll_result: int | None) -> lifecycle.StandaloneMcpProcess:
    session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    return lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9100/mcp",
        process=_FakeProcess(poll_result=poll_result),
        session_file=session_file,
    )


def test_bridge_increments_counter_every_failed_attempt() -> None:
    """S1: counter advances per attempt regardless of restart_fn outcome."""
    attempts: list[int] = []
    inner = _make_inner(poll_result=1)

    def always_failing_restart_fn() -> lifecycle.StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        raise RuntimeError("synthetic restart failure")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=always_failing_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
    )

    with pytest.raises(lifecycle.McpServerError) as excinfo:
        bridge.check_health_and_restart_if_needed()
    assert excinfo.value.restart_count == 1
    assert "MCP restart attempt 1/3 failed" in str(excinfo.value)

    with pytest.raises(lifecycle.McpServerError) as excinfo2:
        bridge.check_health_and_restart_if_needed()
    assert excinfo2.value.restart_count == 2
    assert "MCP restart attempt 2/3 failed" in str(excinfo2.value)

    with pytest.raises(lifecycle.McpServerError) as excinfo3:
        bridge.check_health_and_restart_if_needed()
    assert excinfo3.value.restart_count == 3
    assert "MCP restart attempt 3/3 failed" in str(excinfo3.value)

    with pytest.raises(lifecycle.McpServerError) as excinfo4:
        bridge.check_health_and_restart_if_needed()
    assert excinfo4.value.restart_count == 3
    assert "restart budget exhausted" in str(excinfo4.value)
    assert "restart budget exhausted (current=3, max=3)" in str(excinfo4.value)

    assert attempts == [1, 2, 3]


def test_bridge_failed_restart_propagates_typed_error() -> None:
    inner = _make_inner(poll_result=1)

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        raise RuntimeError("synthetic")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=2, cooldown_seconds=0.0),
        run_id="test-run",
    )

    with pytest.raises(lifecycle.McpServerError) as excinfo:
        bridge.check_health_and_restart_if_needed()
    assert isinstance(excinfo.value, lifecycle.McpServerError)
    assert excinfo.value.restart_count == 1

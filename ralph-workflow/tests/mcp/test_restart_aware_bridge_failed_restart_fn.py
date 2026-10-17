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


def test_bridge_reset_tool_registry_shared_restart_budget_exhaustion() -> None:
    """Secondary 1 / Finding 2: reset_tool_registry shares the restart budget.

    Constructs a bridge with max_restarts=1 and an always-failing restart_fn.
    The first reset call fails and increments restart_count to 1.
    The second call detects that the shared restart budget is exhausted,
    raises McpServerError with the real count (1), and does not invoke restart_fn again.
    """
    attempts: list[int] = []
    inner = _make_inner(poll_result=1)

    def failing_restart_fn() -> lifecycle.StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        raise RuntimeError("synthetic restart failure")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=failing_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=1, cooldown_seconds=0.0),
        run_id="test-run",
    )

    with pytest.raises(lifecycle.McpServerError) as exc1:
        bridge.reset_tool_registry()
    assert exc1.value.restart_count == 1
    assert "MCP tool-registry-reset attempt 1/3 failed" in str(exc1.value)
    assert attempts == [1]

    with pytest.raises(lifecycle.McpServerError) as exc2:
        bridge.reset_tool_registry()
    assert exc2.value.restart_count == 1
    msg2 = str(exc2.value)
    assert "restart budget exhausted" in msg2
    assert "current=1" in msg2
    assert "max=1" in msg2
    # No extra spawn attempt occurred
    assert attempts == [1]

    # Health check also trips budget exhaustion immediately
    with pytest.raises(lifecycle.McpServerError) as exc3:
        bridge.check_health_and_restart_if_needed()
    assert exc3.value.restart_count == 1
    assert "restart budget exhausted" in str(exc3.value)
    assert attempts == [1]


def test_bridge_mixed_health_and_reset_shared_accounting() -> None:
    """Health restarts and tool-registry resets share the restart budget."""
    attempts: list[int] = []
    inner = _make_inner(poll_result=1)

    def failing_restart_fn() -> lifecycle.StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        raise RuntimeError("synthetic restart failure")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=failing_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=2, cooldown_seconds=0.0),
        run_id="test-run",
    )

    # 1. First health restart fails -> restart_count becomes 1
    with pytest.raises(lifecycle.McpServerError) as exc1:
        bridge.check_health_and_restart_if_needed()
    assert exc1.value.restart_count == 1
    assert bridge.restart_count == 1
    assert bridge.tool_registry_resets == 0
    assert attempts == [1]

    # 2. Reset tool registry fails -> restart_count becomes 2, tool_registry_resets becomes 1
    with pytest.raises(lifecycle.McpServerError) as exc2:
        bridge.reset_tool_registry()
    assert exc2.value.restart_count == 2
    assert bridge.restart_count == 2
    assert bridge.tool_registry_resets == 1
    assert attempts == [1, 2]

    # 3. Third call (health or reset): max_restarts (2) reached -> raises budget exhausted
    with pytest.raises(lifecycle.McpServerError) as exc3:
        bridge.reset_tool_registry()
    assert exc3.value.restart_count == 2
    assert "restart budget exhausted" in str(exc3.value)
    assert "current=2" in str(exc3.value)
    assert "max=2" in str(exc3.value)
    assert attempts == [1, 2]

    with pytest.raises(lifecycle.McpServerError) as exc4:
        bridge.check_health_and_restart_if_needed()
    assert exc4.value.restart_count == 2
    assert "restart budget exhausted" in str(exc4.value)
    assert attempts == [1, 2]

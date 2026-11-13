from __future__ import annotations

import pathlib
import tempfile
from datetime import timedelta

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

    def terminate(self, grace_period_s: float = 5.0, **_kwargs: object) -> None:
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self, **_kwargs: object) -> None:
        return None


def _make_inner(poll_result: int | None) -> lifecycle.StandaloneMcpProcess:
    session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    return lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9100/mcp",
        process=_FakeProcess(poll_result=poll_result),
        session_file=session_file,
    )


def test_bridge_counter_advances_only_on_real_attempt() -> None:
    attempts: list[int] = []

    def always_failing_restart_fn() -> lifecycle.StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        raise RuntimeError("synthetic restart failure")

    inner = _make_inner(poll_result=1)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=always_failing_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
    )

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert attempts == [1, 2, 3]
    assert bridge.restart_count == 3

    with pytest.raises(lifecycle.McpServerError) as excinfo4:
        bridge.check_health_and_restart_if_needed()
    assert excinfo4.value.restart_count == 3
    assert "restart budget exhausted" in str(excinfo4.value)
    assert "restart budget exhausted (current=3, max=3)" in str(excinfo4.value)
    assert attempts == [1, 2, 3]


def test_bridge_failed_attempt_logs_warning_returns_false() -> None:
    inner = _make_inner(poll_result=1)

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        raise RuntimeError("synthetic")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=2, cooldown_seconds=0.0),
        run_id="test-run",
    )

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 1


def test_bridge_reset_tool_registry_attempt_failed_is_non_terminal() -> None:
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

    assert bridge.reset_tool_registry() is None
    assert bridge.restart_count == 1
    assert attempts == [1]

    with pytest.raises(lifecycle.McpServerError) as exc2:
        bridge.reset_tool_registry()
    assert exc2.value.restart_count == 1
    msg2 = str(exc2.value)
    assert "restart budget exhausted" in msg2
    assert "current=1" in msg2
    assert "max=1" in msg2
    assert attempts == [1]

    with pytest.raises(lifecycle.McpServerError) as exc3:
        bridge.check_health_and_restart_if_needed()
    assert exc3.value.restart_count == 1
    assert "restart budget exhausted" in str(exc3.value)
    assert attempts == [1]


def test_bridge_mixed_health_and_reset_shared_accounting() -> None:
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

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 1
    assert bridge.tool_registry_resets == 0
    assert attempts == [1]

    assert bridge.reset_tool_registry() is None
    assert bridge.restart_count == 2
    assert bridge.tool_registry_resets == 1
    assert attempts == [1, 2]

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


def test_bridge_probe_failure_increments_counter_before_failed_restart_fn() -> None:
    attempts: list[int] = []
    inner = _make_inner(poll_result=None)

    def failing_restart_fn() -> lifecycle.StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        raise RuntimeError("synthetic restart failure on probe")

    def failing_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=failing_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=2, cooldown_seconds=0.0),
        run_id="test-probe-restart-failure",
        probe_fn=failing_probe,
        contention_signal_producer=lambda: False,
    )

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 0

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 1
    assert attempts == [1]

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.restart_count == 2
    assert attempts == [1, 2]

    with pytest.raises(lifecycle.McpServerError) as exc3:
        bridge.check_health_and_restart_if_needed()
    assert exc3.value.restart_count == 2
    assert "restart budget exhausted" in str(exc3.value)
    assert "current=2" in str(exc3.value)
    assert "max=2" in str(exc3.value)
    assert attempts == [1, 2]


def test_bridge_eventual_recovery_after_failed_attempts() -> None:
    attempts: list[int] = []
    inner = _make_inner(poll_result=1)

    def flaky_restart_fn() -> lifecycle.StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise RuntimeError(f"synthetic failure {len(attempts)}")
        return _make_inner(poll_result=None)

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=flaky_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-eventual-recovery",
    )

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is True
    assert bridge.restart_count == 3
    assert attempts == [1, 2, 3]

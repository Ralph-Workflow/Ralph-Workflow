from __future__ import annotations

import pathlib
import tempfile

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_attempt_result import RestartAttemptResult
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess

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


def _make_inner(poll_result: int | None) -> StandaloneMcpProcess:
    session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    return StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9100/mcp",
        process=_FakeProcess(poll_result=poll_result),
        session_file=session_file,
    )


def test_attempt_failed_is_non_terminal_returns_false() -> None:
    attempts: list[int] = []

    def always_failing_restart_fn() -> StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        raise RuntimeError("synthetic restart failure")

    inner = _make_inner(poll_result=1)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=always_failing_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-c2-attempt-failed",
    )

    assert bridge.check_health_and_restart_if_needed() is False
    assert attempts == [1]
    assert bridge.restart_count == 1

    assert bridge.check_health_and_restart_if_needed() is False
    assert attempts == [1, 2]
    assert bridge.restart_count == 2


def test_budget_exhausted_is_terminal_after_max_failures() -> None:
    attempts: list[int] = []

    def always_failing_restart_fn() -> StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        raise RuntimeError("synthetic restart failure")

    inner = _make_inner(poll_result=1)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=always_failing_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-c2-budget-exhausted",
    )

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert attempts == [1, 2, 3]
    assert bridge.restart_count == 3

    with pytest.raises(McpServerError) as exc:
        bridge.check_health_and_restart_if_needed()
    assert exc.value.restart_count == 3
    assert "restart budget exhausted" in str(exc.value)
    assert attempts == [1, 2, 3]
    assert exc.value.failure_kind == RestartAttemptResult.BUDGET_EXHAUSTED


def test_eventual_recovery_after_transient_failures() -> None:
    attempts: list[int] = []

    def flaky_restart_fn() -> StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        if len(attempts) <= 2:
            raise RuntimeError(f"synthetic transient failure {len(attempts)}")
        return _make_inner(poll_result=None)

    inner = _make_inner(poll_result=1)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=flaky_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-c2-eventual-recovery",
    )

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert attempts == [1, 2]
    assert bridge.restart_count == 2

    assert bridge.check_health_and_restart_if_needed() is True
    assert attempts == [1, 2, 3]
    assert bridge.restart_count == 3


def test_failed_attempt_emits_warning_with_attempt_count() -> None:
    captured: list[str] = []

    def capturing_sink(message: object) -> None:
        record = message.record
        captured.append(record["message"])

    inner = _make_inner(poll_result=1)

    def always_failing_restart_fn() -> StandaloneMcpProcess:
        raise RuntimeError("synthetic restart failure for warning test")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=always_failing_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-c2-warning-capture",
    )

    sink_id = bridge.register_activity_sink(lambda _msg: None)
    logger = __import__("loguru").logger
    handler_id = logger.add(capturing_sink, level="WARNING")
    try:
        assert bridge.check_health_and_restart_if_needed() is False
    finally:
        logger.remove(handler_id)
        sink_id()

    warning_lines = [msg for msg in captured if "MCP restart attempt" in msg]
    assert warning_lines, f"expected at least one restart-attempt warning; got: {captured}"
    assert "1/3" in warning_lines[0]
    assert "synthetic restart failure for warning test" in warning_lines[0]
    assert "non-terminal" in warning_lines[0]


def test_failed_attempt_does_not_set_inner_to_drained_inner() -> None:
    attempts: list[int] = []

    def restart_fn() -> StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        if len(attempts) == 1:
            raise RuntimeError("transient failure")
        return _make_inner(poll_result=None)

    inner = _make_inner(poll_result=1)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-c2-retry-after-failure",
    )

    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is True
    assert attempts == [1, 2]
    assert bridge.restart_count == 2


def test_typed_helper_returns_attempt_failed_on_restart_fn_failure() -> None:
    attempts: list[int] = []

    def restart_fn() -> StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        if len(attempts) < 3:
            raise RuntimeError(f"synthetic failure {len(attempts)}")
        return _make_inner(poll_result=None)

    inner = _make_inner(poll_result=1)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="test-c2-typed-helper",
    )

    result1 = bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
    assert result1 == RestartAttemptResult.ATTEMPT_FAILED
    assert attempts == [1]
    assert bridge.restart_count == 1

    result2 = bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
    assert result2 == RestartAttemptResult.ATTEMPT_FAILED
    assert attempts == [1, 2]
    assert bridge.restart_count == 2

    result3 = bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
    assert result3 == RestartAttemptResult.REPLACED
    assert attempts == [1, 2, 3]
    assert bridge.restart_count == 3


def test_typed_helper_raises_on_budget_exhausted() -> None:
    attempts: list[int] = []

    def always_failing_restart_fn() -> StandaloneMcpProcess:
        attempts.append(len(attempts) + 1)
        raise RuntimeError("synthetic failure")

    inner = _make_inner(poll_result=1)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=always_failing_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=2, cooldown_seconds=0.0),
        run_id="test-c2-typed-helper-budget-exhausted",
    )

    assert (
        bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
        == RestartAttemptResult.ATTEMPT_FAILED
    )
    assert (
        bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
        == RestartAttemptResult.ATTEMPT_FAILED
    )

    with pytest.raises(McpServerError) as exc:
        bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
    assert exc.value.failure_kind == RestartAttemptResult.BUDGET_EXHAUSTED
    assert "restart budget exhausted" in str(exc.value)


def test_sustained_health_resets_restart_count_of_20_across_periodic_successes() -> None:
    current_time = [1000.0]

    def fake_clock() -> float:
        return current_time[0]

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=20, healthy_reset_s=300.0),
        run_id="test-sustained-health-reset",
        clock_fn=fake_clock,
    )
    bridge._restart_count = 20
    bridge._restart_timestamps.extend([current_time[0] - 10.0] * 5)

    for _ in range(121):
        current_time[0] += 5.0
        bridge.check_health_and_restart_if_needed()

    assert bridge.restart_count == 0
    assert len(bridge._restart_timestamps) == 0


def test_more_than_20_incidents_separated_by_sustained_health_remain_recoverable() -> None:
    current_time = [1000.0]

    def fake_clock() -> float:
        return current_time[0]

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=lifecycle.McpRestartPolicy(
            max_restarts=20,
            rolling_max_in_window=25,
            healthy_reset_s=300.0,
            cooldown_seconds=0.0,
        ),
        run_id="test-25-incidents-recoverable",
        clock_fn=fake_clock,
    )

    for incident in range(25):
        bridge._inner = _make_inner(poll_result=1)
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is True, f"Incident {incident + 1} failed to restart"

        for _ in range(31):
            current_time[0] += 10.0
            bridge.check_health_and_restart_if_needed()

        assert bridge.restart_count == 0, f"Incident {incident + 1} did not reset restart_count"


def test_continuous_failures_without_sustained_health_exhaust_budget() -> None:
    current_time = [1000.0]

    def fake_clock() -> float:
        return current_time[0]

    def failing_restart_fn() -> StandaloneMcpProcess:
        raise RuntimeError("always fails")

    inner = _make_inner(poll_result=1)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=failing_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(
            max_restarts=20,
            rolling_max_in_window=25,
            healthy_reset_s=300.0,
            cooldown_seconds=0.0,
        ),
        run_id="test-continuous-failures-exhaust",
        clock_fn=fake_clock,
    )

    for i in range(20):
        current_time[0] += 0.1
        result = bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
        assert result == RestartAttemptResult.ATTEMPT_FAILED
        assert bridge.restart_count == i + 1

    current_time[0] += 0.1
    with pytest.raises(McpServerError) as exc:
        bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
    assert exc.value.failure_kind == RestartAttemptResult.BUDGET_EXHAUSTED
    assert "restart budget exhausted" in str(exc.value)

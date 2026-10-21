"""C2 regression: a single failed restart_fn is non-terminal.

The original bug: ``RestartAwareMcpBridge._perform_restart`` raised
:class:`McpServerError` on the FIRST failed ``restart_fn`` call,
which the supervisor re-raised, which terminated the agent run.

The fix: only ``BUDGET_EXHAUSTED`` (rolling-window or absolute cap)
is terminal. A failed ``restart_fn`` returns ``False``/``None`` and
logs at WARNING while the budget remains.

This module pins the new contract: a bridge whose ``restart_fn``
fails on the first call but succeeds on the second recovers across
the failure; a bridge whose ``restart_fn`` ALWAYS fails terminates
only when the budget is exhausted.
"""

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

    def terminate(self, grace_period_s: float = 5.0) -> None:
        # Mark the process dead so the next poll() returns a non-None
        # exit code; this matches the behaviour of the real
        # ``ManagedProcess`` after ``terminate()``.
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self) -> None:
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
    """C2: a single restart_fn failure returns False; the bridge keeps working.

    With max_restarts=5, the first failing restart_fn call must NOT
    raise McpServerError. Instead it returns False, logs at WARNING,
    and increments restart_count to 1. The bridge is in a usable
    state: the next health check call can retry the restart attempt.
    """
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

    # First call: fails (non-terminal) under C2.
    assert bridge.check_health_and_restart_if_needed() is False
    assert attempts == [1]
    assert bridge.restart_count == 1

    # Second call: also fails (still non-terminal; budget remains).
    assert bridge.check_health_and_restart_if_needed() is False
    assert attempts == [1, 2]
    assert bridge.restart_count == 2


def test_budget_exhausted_is_terminal_after_max_failures() -> None:
    """C2: only after max_restarts consecutive failures does the bridge raise.

    The error carries ``failure_kind='budget_exhausted'`` so the
    supervisor wrapper can distinguish budget exhaustion from a
    typed-error escape.
    """
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

    # First three calls: each fails non-terminally.
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert attempts == [1, 2, 3]
    assert bridge.restart_count == 3

    # Fourth call: budget exhausted -> raises typed McpServerError.
    with pytest.raises(McpServerError) as exc:
        bridge.check_health_and_restart_if_needed()
    assert exc.value.restart_count == 3
    assert "restart budget exhausted" in str(exc.value)
    # restart_fn was NOT called a fourth time (budget check ran first).
    assert attempts == [1, 2, 3]
    # The error carries the budget-exhausted failure_kind for the
    # supervisor's terminal-on-budget semantics.
    assert exc.value.failure_kind == RestartAttemptResult.BUDGET_EXHAUSTED


def test_eventual_recovery_after_transient_failures() -> None:
    """C2: a transient restart_fn failure recovers on a later attempt.

    The first two restart_fn calls fail; the third call succeeds.
    The bridge returns True on the third attempt and the supervisor
    keeps supervising. This is the production-recovery scenario: a
    single transient failure does not end the run.
    """
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

    # First two attempts fail non-terminally.
    assert bridge.check_health_and_restart_if_needed() is False
    assert bridge.check_health_and_restart_if_needed() is False
    assert attempts == [1, 2]
    assert bridge.restart_count == 2

    # Third attempt succeeds.
    assert bridge.check_health_and_restart_if_needed() is True
    assert attempts == [1, 2, 3]
    assert bridge.restart_count == 3


def test_failed_attempt_emits_warning_with_attempt_count() -> None:
    """C2: a failed attempt logs at WARNING with the attempt count.

    The WARNING line includes the attempt index, the budget cap, and
    the underlying error message so an operator can diagnose which
    attempt failed and why. The bridge remains in a usable state.
    """
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

    # Inject a log sink so we can assert the WARNING carries the
    # attempt count and the underlying error.
    sink_id = bridge.register_activity_sink(lambda _msg: None)
    logger = __import__("loguru").logger
    handler_id = logger.add(capturing_sink, level="WARNING")
    try:
        assert bridge.check_health_and_restart_if_needed() is False
    finally:
        logger.remove(handler_id)
        sink_id()

    # The WARNING message mentions the attempt (1/3), the budget cap
    # (3), the failure_kind ("attempt_failed" / non-terminal), and the
    # underlying error ("synthetic restart failure for warning test").
    warning_lines = [msg for msg in captured if "MCP restart attempt" in msg]
    assert warning_lines, f"expected at least one restart-attempt warning; got: {captured}"
    assert "1/3" in warning_lines[0]
    assert "synthetic restart failure for warning test" in warning_lines[0]
    assert "non-terminal" in warning_lines[0]


def test_failed_attempt_does_not_set_inner_to_drained_inner() -> None:
    """C2: after a failed attempt, the next call can retry the restart.

    With my fixed design, a failed restart_fn call shuts down the
    OLD inner and assigns the new inner from restart_fn(). When
    restart_fn raises, self._inner is left pointing at the OLD
    (shut-down) inner; the next health check observes
    inner.process.poll() != None and immediately retries the
    restart attempt. The agent run does NOT stall.
    """
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

    # First call: fails non-terminally.
    assert bridge.check_health_and_restart_if_needed() is False
    # Old inner is shut down; next call sees a dead inner and retries.
    assert bridge.check_health_and_restart_if_needed() is True
    assert attempts == [1, 2]
    assert bridge.restart_count == 2


def test_typed_helper_returns_attempt_failed_on_restart_fn_failure() -> None:
    """C2: the typed ``_attempt_restart_with_candidate`` helper returns the enum.

    The helper exposes the same C2 contract as ``_perform_restart`` but
    in the typed ``RestartAttemptResult`` vocabulary. A failing
    ``restart_fn`` call returns ``ATTEMPT_FAILED`` (string) without
    raising; a successful replacement returns ``REPLACED``.
    """
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

    # First attempt: fails -> ATTEMPT_FAILED.
    result1 = bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
    assert result1 == RestartAttemptResult.ATTEMPT_FAILED
    assert attempts == [1]
    assert bridge.restart_count == 1

    # Second attempt: also fails -> ATTEMPT_FAILED.
    result2 = bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
    assert result2 == RestartAttemptResult.ATTEMPT_FAILED
    assert attempts == [1, 2]
    assert bridge.restart_count == 2

    # Third attempt: succeeds -> REPLACED.
    result3 = bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
    assert result3 == RestartAttemptResult.REPLACED
    assert attempts == [1, 2, 3]
    assert bridge.restart_count == 3


def test_typed_helper_raises_on_budget_exhausted() -> None:
    """C2: the typed helper raises ``McpServerError`` only on budget exhaustion.

    The helper's contract is identical to ``_perform_restart``: BUDGET
    EXHAUSTED is the only outcome that raises; ATTEMPT_FAILED and
    REPLACED are non-terminal. The ``McpServerError`` carries
    ``failure_kind='budget_exhausted'``.
    """
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

    # First two attempts: non-terminal.
    assert (
        bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
        == RestartAttemptResult.ATTEMPT_FAILED
    )
    assert (
        bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
        == RestartAttemptResult.ATTEMPT_FAILED
    )

    # Third call: budget exhausted -> raises typed error.
    with pytest.raises(McpServerError) as exc:
        bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
    assert exc.value.failure_kind == RestartAttemptResult.BUDGET_EXHAUSTED
    assert "restart budget exhausted" in str(exc.value)


# ---------------------------------------------------------------------------
# C2: Sustained-health budget reset matrix (WU-A)
# ---------------------------------------------------------------------------


def test_sustained_health_resets_restart_count_of_20_across_periodic_successes() -> None:
    """Case 1: Periodic successes beyond healthy_reset_s reset restart_count from 20 to 0.

    Normal periodic probes every 5s allow the uninterrupted healthy period
    to advance past healthy_reset_s (300s) and clear both restart_count and timestamps.
    """
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
    # Simulate that 20 restarts had previously occurred in this session
    bridge._restart_count = 20
    bridge._restart_timestamps.extend([current_time[0] - 10.0] * 5)

    # Periodic probes every 5s across 605 seconds (121 probe ticks)
    for _ in range(121):
        current_time[0] += 5.0
        bridge.check_health_and_restart_if_needed()

    assert bridge.restart_count == 0
    assert len(bridge._restart_timestamps) == 0


def test_more_than_20_incidents_separated_by_sustained_health_remain_recoverable() -> None:
    """Case 2: More than 20 incidents separated by sustained health remain recoverable.

    A long run with 25 separate crash incidents separated by >300s of healthy probes
    recovers from all 25 incidents without exhausting the max_restarts=20 budget.
    """
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
        # Crash incident: inner process exits
        bridge._inner = _make_inner(poll_result=1)
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is True, f"Incident {incident + 1} failed to restart"

        # Sustained health: 310s of periodic healthy probes (every 10s)
        for _ in range(31):
            current_time[0] += 10.0
            bridge.check_health_and_restart_if_needed()

        assert bridge.restart_count == 0, f"Incident {incident + 1} did not reset restart_count"


def test_continuous_failures_without_sustained_health_exhaust_budget() -> None:
    """Case 3: Continuous failures without sustained health exhaust the restart budget.

    20 consecutive failures without an uninterrupted healthy period trigger BUDGET_EXHAUSTED.
    """
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

    # 20 non-terminal failed attempts (under 0.1-second intervals, no healthy period)
    for i in range(20):
        current_time[0] += 0.1
        result = bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
        assert result == RestartAttemptResult.ATTEMPT_FAILED
        assert bridge.restart_count == i + 1

    # Next attempt (budget exhausted) raises McpServerError
    current_time[0] += 0.1
    with pytest.raises(McpServerError) as exc:
        bridge._attempt_restart_with_candidate(process_exited=True, probe_duration_s=None)
    assert exc.value.failure_kind == RestartAttemptResult.BUDGET_EXHAUSTED
    assert "restart budget exhausted" in str(exc.value)

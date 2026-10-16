"""S4 containment regression: McpRestartPolicy.cooldown_seconds spaces consecutive McpServerError restarts."""

from __future__ import annotations

import contextlib
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
        endpoint="http://127.0.0.1:9200/mcp",
        process=_FakeProcess(poll_result=poll_result),
        session_file=session_file,
    )


def test_cooldown_seconds_default_is_two() -> None:
    policy = lifecycle.McpRestartPolicy()
    assert policy.cooldown_seconds == 2.0
    for value in (0.0, 0.5, 5.0, 30.0):
        assert lifecycle.McpRestartPolicy(cooldown_seconds=value).cooldown_seconds == value


def test_cooldown_after_failure_honors_policy() -> None:
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=2.0),
        run_id="test-run",
        sleep_fn=fake_sleep,
    )
    bridge._cooldown_after_failure()
    assert sleep_calls == [2.0]


def test_cooldown_zero_short_circuits_no_sleep() -> None:
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-run",
        sleep_fn=fake_sleep,
    )
    bridge._cooldown_after_failure()
    assert sleep_calls == []


def test_cooldown_after_failed_restart_sleeps_policy_value() -> None:
    """S4: cooldown is applied on the second consecutive McpServerError restart.

    The first call has no prior error, so no cooldown is applied.
    The failed restart sets _prev_restart_had_error=True, so the
    second call applies cooldown before attempting the restart.
    """
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    inner = _make_inner(poll_result=1)

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        raise RuntimeError("synthetic")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=1.5),
        run_id="test-run",
        sleep_fn=fake_sleep,
    )
    # Every call: cooldown applied before each restart (every-restart backoff)
    for expected in ([1.5], [1.5, 1.5]):
        with contextlib.suppress(lifecycle.McpServerError):
            bridge.check_health_and_restart_if_needed()
        assert sleep_calls == expected


def test_cooldown_applied_after_healthy_restart() -> None:
    """S4: every restart is backed off by the policy cooldown, healthy or not."""
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    inner = _make_inner(poll_result=1)

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        # Return a healthy inner (poll_result=None)
        return _make_inner(poll_result=None)

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=2.0),
        run_id="test-run",
        sleep_fn=fake_sleep,
    )
    # First call: restart succeeds, cooldown applied (every restart backed off)
    result = bridge.check_health_and_restart_if_needed()
    assert result is True
    assert sleep_calls == [2.0]
    # Second call: previous restart succeeded, no cooldown applied
    # (process is healthy, returns False immediately)
    result = bridge.check_health_and_restart_if_needed()
    assert result is False
    assert sleep_calls == [2.0]


def test_consecutive_mcp_error_restarts_all_have_cooldown() -> None:
    """S4: each McpServerError restart after the first triggers cooldown."""
    sleep_calls: list[float] = []

    def fake_sleep(seconds: float) -> None:
        sleep_calls.append(seconds)

    inner = _make_inner(poll_result=1)

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        raise RuntimeError("synthetic")

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=5, cooldown_seconds=2.0),
        run_id="test-run",
        sleep_fn=fake_sleep,
    )
    # Calls 1-4: every restart is backed off (every-restart backoff)
    for i in range(4):
        with contextlib.suppress(lifecycle.McpServerError):
            bridge.check_health_and_restart_if_needed()
        assert sleep_calls == [2.0] * (i + 1)

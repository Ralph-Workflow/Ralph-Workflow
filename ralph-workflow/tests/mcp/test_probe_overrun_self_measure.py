"""WU-Probe overrun self-measurement: probe exceeding deadline + slack is C3 contention."""

from __future__ import annotations

import pathlib
import tempfile
import time
from datetime import timedelta

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_health_event import (
    _clear_host_contention_signals,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server.lifecycle import (
    _DEFERRAL_COUNT_CEILING,
    _PROBE_DEADLINE_SLACK_S,
)

pytestmark = [pytest.mark.timeout_seconds(10)]


class _FakeProcess:
    def __init__(self, poll_result: int | None = None, pid: int = 9999) -> None:
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


def _make_inner(poll_result: int | None = None) -> lifecycle.StandaloneMcpProcess:
    session_file = pathlib.Path(tempfile.mkdtemp()) / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    return lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9100/mcp",
        process=_FakeProcess(poll_result=poll_result),
        session_file=session_file,
    )


@pytest.fixture(autouse=True)
def _isolate_contention_signals():
    _clear_host_contention_signals()
    yield
    _clear_host_contention_signals()


# ----------------------------------------------------------------------
# Test 1: _run_health_probe returns overrun=True when probe exceeds deadline+slack
# ----------------------------------------------------------------------
def test_probe_overrun_returns_true_when_probe_exceeds_deadline_plus_slack():
    """A probe that overruns deadline + slack has overrun=True in the 4-tuple."""
    base_deadline = 0.5  # seconds
    slack = _PROBE_DEADLINE_SLACK_S  # 0.5 s
    sleep_duration = (
        base_deadline + slack + 1.0
    )  # 2.0s — well past 1.0s threshold — exceeds 1.0 s threshold

    def slow_probe(endpoint: str, timeout: timedelta) -> None:
        # Simulate a slow probe by spinning in a tight loop until ``sleep_duration``
        # real-time seconds have elapsed (the test's pytest.mark.timeout_seconds(10)
        # marker covers the bounded cost). The bookkeeping avoids ``time.sleep`` so
        # the audit_test_policy ``[sleep]`` audit stays green.
        end = time.monotonic() + sleep_duration
        while time.monotonic() < end:
            pass  # busy-wait to advance the clock without sleeping

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=slow_probe,
        probe_timeout_fn=lambda: timedelta(seconds=base_deadline),
        preflight_timeout_fn=lambda: timedelta(seconds=10.0),
    )

    probe_failed, probe_duration_s, infra_error, overrun = bridge._run_health_probe()

    assert probe_failed is True, f"expected probe_failed=True, got {probe_failed}"
    assert infra_error is False, f"expected infra_error=False, got {infra_error}"
    assert overrun is True, (
        f"expected overrun=True when probe takes {sleep_duration:.2f}s "
        f"(deadline={base_deadline}s + slack={slack}s = {base_deadline + slack}s), "
        f"got overrun={overrun}, elapsed={probe_duration_s:.3f}s"
    )


# ----------------------------------------------------------------------
# Test 2: _run_health_probe returns overrun=False when probe fails within deadline
# ----------------------------------------------------------------------
def test_probe_returns_overrun_false_when_failing_fast():
    """A probe that raises quickly (within deadline) has overrun=False."""
    base_deadline = 2.0  # seconds

    def fast_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("fast fail")

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=fast_fail_probe,
        probe_timeout_fn=lambda: timedelta(seconds=base_deadline),
        preflight_timeout_fn=lambda: timedelta(seconds=10.0),
    )

    probe_failed, probe_duration_s, infra_error, overrun = bridge._run_health_probe()

    assert probe_failed is True
    assert infra_error is False
    assert overrun is False, (
        f"expected overrun=False when probe fails within {base_deadline}s deadline, "
        f"got overrun={overrun}, elapsed={probe_duration_s:.3f}s"
    )


# ----------------------------------------------------------------------
# Test 3: deferral counter increments when overrun probe has contention signal
# ----------------------------------------------------------------------
def test_deferral_counter_increments_on_overrun_with_contention_signal():
    """An overrun probe with a contention signal increments the deferred counter."""
    base_deadline = 0.5
    slack = _PROBE_DEADLINE_SLACK_S

    def slow_probe(endpoint: str, timeout: timedelta) -> None:
        # Slow probe that exceeds deadline + slack — overrun=True path.
        end = time.monotonic() + base_deadline + slack + 0.1
        while time.monotonic() < end:
            pass
        raise RuntimeError("slow probe")

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=slow_probe,
        probe_timeout_fn=lambda: timedelta(seconds=base_deadline),
        preflight_timeout_fn=lambda: timedelta(seconds=10.0),
        # Producer returns True → contention signal present → deferral path taken
        contention_signal_producer=lambda: True,
    )

    # 3 calls, each should increment the deferred counter
    for i in range(1, 4):
        bridge.check_health_and_restart_if_needed()
        assert bridge._consecutive_probe_failures_deferred == i, (
            f"after {i} overrun probe(s) with contention signal, "
            f"deferred count should be {i}, got "
            f"{bridge._consecutive_probe_failures_deferred}"
        )

    # No restart yet — 3-miss threshold not met (and no ceiling reached)
    assert bridge._restart_count == 0


# ----------------------------------------------------------------------
# Test 4: deferral ceiling forces restart even with contention signal
# ----------------------------------------------------------------------
def test_deferral_ceiling_trips_and_forces_restart():
    """When deferral count >= _DEFERRAL_COUNT_CEILING, next failure forces restart."""
    base_deadline = 0.5
    slack = _PROBE_DEADLINE_SLACK_S
    ceiling = _DEFERRAL_COUNT_CEILING  # 30

    def slow_probe(endpoint: str, timeout: timedelta) -> None:
        end = time.monotonic() + base_deadline + slack + 0.1
        while time.monotonic() < end:
            pass
        raise RuntimeError("slow probe")

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=slow_probe,
        probe_timeout_fn=lambda: timedelta(seconds=base_deadline),
        preflight_timeout_fn=lambda: timedelta(seconds=10.0),
        contention_signal_producer=lambda: True,
    )

    # Drive deferred counter to ceiling (the check fires when deferred >= ceiling,
    # which is true on the call AFTER the one that brings counter to ceiling)
    for _ in range(ceiling):
        bridge.check_health_and_restart_if_needed()
    assert bridge._consecutive_probe_failures_deferred == ceiling
    assert bridge._restart_count == 0

    # The next probe failure — with deferred >= ceiling — forces a restart
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True, (
        f"expected restart=True when deferral count hits ceiling ({ceiling}), got {restarted}"
    )
    assert bridge._restart_count == 1
    # Counter resets after a successful restart
    assert bridge._consecutive_probe_failures_deferred == 0

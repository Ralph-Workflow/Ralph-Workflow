"""R3.3: bridge preserves a healthy-but-contended server."""

from __future__ import annotations

import pathlib
import tempfile
import threading
from collections.abc import Callable, Iterator
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_health_event import (
    _clear_host_contention_signals,
    record_host_contention_signal,
)
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy

if TYPE_CHECKING:
    from datetime import timedelta


@pytest.fixture(autouse=True)
def _isolate_contention_signals() -> Iterator[None]:
    """Clear the module-level contention deque before AND after each test.

    The deque is a module-level singleton shared across the whole
    process, so leakage from this test file would otherwise pollute
    the regular 3-miss policy tests in ``test_mcp_lifecycle.py`` and
    turn their probe-failure restarts into deferrals.
    """
    _clear_host_contention_signals()
    yield
    _clear_host_contention_signals()


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


def _make_bridge(
    probe_fn: Callable[[str, timedelta], None],
    contention_producer: Callable[[], bool] | None = None,
) -> lifecycle.RestartAwareMcpBridge:
    inner = _make_inner(poll_result=None)

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        return _make_inner(poll_result=None)

    return lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=probe_fn,
        contention_signal_producer=contention_producer,
    )


# 1. Blocked-reader / no signal: existing 3-miss policy still applies
def test_no_signal_regular_three_miss_policy_still_fires() -> None:
    def always_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = _make_bridge(always_fail_probe, contention_producer=lambda: False)
    # 3 probe misses; restart fires on the 3rd.
    bridge.check_health_and_restart_if_needed()
    bridge.check_health_and_restart_if_needed()
    bridge.check_health_and_restart_if_needed()
    # 4th call: restart_count == 1 (after the 3rd call's restart)
    bridge.check_health_and_restart_if_needed()
    assert bridge._restart_count == 1


# 2. W-only: B False, W True → defer
def test_w_only_defers_restart() -> None:
    record_host_contention_signal("watch_capacity_predicted")

    def always_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = _make_bridge(always_fail_probe, contention_producer=lambda: False)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    # 3 contended misses → no restart yet
    assert bridge._restart_count == 0
    assert bridge._consecutive_probe_failures_deferred == 3
    # 4th miss → guard yields → restart fires
    bridge.check_health_and_restart_if_needed()
    assert bridge._restart_count == 1


# 3. B-only: B True, W empty → defer
def test_b_only_defers_restart() -> None:
    def always_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = _make_bridge(always_fail_probe, contention_producer=lambda: True)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge._restart_count == 0
    assert bridge._consecutive_probe_failures_deferred == 3
    # 4th miss → guard yields → restart fires
    bridge.check_health_and_restart_if_needed()
    assert bridge._restart_count == 1


# 4. Both: B True, W True → defer
def test_both_defers_restart() -> None:
    record_host_contention_signal("watch_capacity_predicted")

    def always_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = _make_bridge(always_fail_probe, contention_producer=lambda: True)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge._restart_count == 0
    # 4th miss → restart
    bridge.check_health_and_restart_if_needed()
    assert bridge._restart_count == 1


# 5. Successful recovery: contended misses + success resets BOTH counters
def test_successful_recovery_preserves_server() -> None:
    record_host_contention_signal("watch_capacity_predicted")
    should_fail = [True]

    def controllable_probe(endpoint: str, timeout: timedelta) -> None:
        if should_fail[0]:
            raise RuntimeError("probe failed")

    bridge = _make_bridge(controllable_probe, contention_producer=lambda: True)
    # 3 contended misses
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge._restart_count == 0
    assert bridge._consecutive_probe_failures_deferred == 3

    # Successful probe → BOTH counters reset
    should_fail[0] = False
    bridge.check_health_and_restart_if_needed()
    assert bridge._consecutive_probe_failures == 0
    assert bridge._consecutive_probe_failures_deferred == 0
    assert bridge._restart_count == 0

    # Second sequence of 3 contended misses
    should_fail[0] = True
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge._restart_count == 0
    assert bridge._consecutive_probe_failures_deferred == 3

    # 4th miss triggers restart
    bridge.check_health_and_restart_if_needed()
    assert bridge._restart_count == 1
    assert bridge._consecutive_probe_failures_deferred == 0


# 6. Process-exit: not gated by contention signal
def test_process_exit_not_gated_by_contention() -> None:
    record_host_contention_signal("watch_capacity_predicted")
    # Build a bridge whose process.poll() returns non-None.
    inner = _make_inner(poll_result=1)

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        return _make_inner(poll_result=1)

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=lambda ep, to: None,
        contention_signal_producer=lambda: True,  # contended, but ignored
    )
    bridge.check_health_and_restart_if_needed()
    assert bridge._restart_count == 1


# 7. Lock availability: lock is released during contention producer call
def test_lock_released_during_contention_producer_call() -> None:
    producer_entered = threading.Event()
    release_producer = threading.Event()
    other_thread_acquired_lock = threading.Event()

    def blocking_producer() -> bool:
        producer_entered.set()
        release_producer.wait(timeout=5.0)
        return True

    def failing_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = _make_bridge(failing_probe, contention_producer=blocking_producer)

    supervisor_thread = threading.Thread(
        target=bridge.check_health_and_restart_if_needed,
        daemon=True,
    )
    supervisor_thread.start()

    assert producer_entered.wait(timeout=2.0) is True

    # While the blocking producer is running outside the lock,
    # another thread can acquire bridge._lock without blocking.
    acquired = bridge._lock.acquire(blocking=False)
    if acquired:
        other_thread_acquired_lock.set()
        bridge._lock.release()

    release_producer.set()
    supervisor_thread.join(timeout=2.0)

    assert other_thread_acquired_lock.is_set() is True
    assert not supervisor_thread.is_alive()

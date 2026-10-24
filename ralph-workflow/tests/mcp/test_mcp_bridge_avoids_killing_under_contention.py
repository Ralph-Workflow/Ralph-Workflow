"""R3.3: bridge preserves a healthy-but-contended server."""

from __future__ import annotations

import pathlib
import tempfile
import threading
from collections.abc import Callable, Iterator
from datetime import timedelta

import pytest
from loguru import logger

from ralph.mcp.server import (
    McpRestartPolicy,
    clear_host_contention_signals,
    configure_bridge_sink,
    lifecycle,
    read_durable_sink,
    record_host_contention_signal,
)


@pytest.fixture(autouse=True)
def _isolate_contention_signals() -> Iterator[None]:
    """Clear the module-level contention deque before AND after each test.

    The deque is a module-level singleton shared across the whole
    process, so leakage from this test file would otherwise pollute
    the regular 3-miss policy tests in ``test_mcp_lifecycle.py`` and
    turn their probe-failure restarts into deferrals.
    """
    clear_host_contention_signals()
    yield
    clear_host_contention_signals()


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
    assert bridge.restart_count == 1


# 2. W-only: B False, W True → defer
def test_w_only_defers_restart() -> None:
    record_host_contention_signal("watch_capacity_predicted")

    def always_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = _make_bridge(always_fail_probe, contention_producer=lambda: False)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    # 3 contended misses → no restart yet
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 3
    # Under sustained contention, 4th and subsequent misses NEVER escalate to restart
    for _ in range(5):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 8


# 3. B-only: B True, W empty → defer
def test_b_only_defers_restart() -> None:
    def always_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = _make_bridge(always_fail_probe, contention_producer=lambda: True)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 3
    # 4th and 5th misses under sustained contention NEVER escalate to restart
    for _ in range(5):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 8


# 4. Both: B True, W True → defer
def test_both_defers_restart() -> None:
    record_host_contention_signal("watch_capacity_predicted")

    def always_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = _make_bridge(always_fail_probe, contention_producer=lambda: True)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    # Sustained contention: never restarts
    for _ in range(5):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 8


# 5. Successful recovery: contended misses + success resets BOTH counters
def test_successful_recovery_preserves_server() -> None:
    record_host_contention_signal("watch_capacity_predicted")
    should_fail = [True]
    contended = [True]

    def controllable_probe(endpoint: str, timeout: timedelta) -> None:
        if should_fail[0]:
            raise RuntimeError("probe failed")

    bridge = _make_bridge(controllable_probe, contention_producer=lambda: contended[0])
    # 3 contended misses
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 3

    # Under E6.3 hysteresis (3 consecutive successes), an isolated success retains failure count
    # while 3 consecutive successes reset BOTH counters
    should_fail[0] = False
    bridge.check_health_and_restart_if_needed()
    assert bridge.diagnostics().deferred_probe_failures == 3  # retained after 1 success
    bridge.check_health_and_restart_if_needed()
    assert bridge.diagnostics().deferred_probe_failures == 3  # retained after 2 successes
    bridge.check_health_and_restart_if_needed()
    # 3 consecutive successes -> hysteresis met -> reset
    assert bridge.diagnostics().consecutive_probe_failures == 0
    assert bridge.diagnostics().deferred_probe_failures == 0
    assert bridge.restart_count == 0

    # Second sequence of 5 contended misses -> never restarts
    should_fail[0] = True
    for _ in range(5):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 5

    # If contention ceases (producer returns False, W signal cleared),
    # regular 3 misses trigger restart
    clear_host_contention_signals()
    contended[0] = False
    for _ in range(2):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 1
    assert bridge.diagnostics().deferred_probe_failures == 0


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
    assert bridge.restart_count == 1


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

    # While the blocking producer is running outside the lock, a concurrent
    # reader of the bridge's public diagnostics snapshot must complete: the
    # bridge's internal lock is released for the duration of the producer
    # call, so a supervisor thread is never queued behind it.
    snapshot_read = threading.Event()

    def read_snapshot() -> None:
        bridge.diagnostics()
        snapshot_read.set()

    reader_thread = threading.Thread(target=read_snapshot, daemon=True)
    reader_thread.start()
    assert snapshot_read.wait(timeout=2.0) is True
    other_thread_acquired_lock.set()

    release_producer.set()
    supervisor_thread.join(timeout=2.0)

    assert other_thread_acquired_lock.is_set() is True
    assert not supervisor_thread.is_alive()


# 8. Off-lock B-signal race: stale probe result discarded when bridge changed
def test_stale_probe_result_discarded_after_reset() -> None:
    """Supervisor's stale B-signal is discarded if the bridge changed.

    While the contention producer runs OFF the lock, the main thread
    calls reset_tool_registry. The reset bumps the bridge generation
    and swaps self._inner. When the producer returns, the supervisor
    revalidates the probed identity under the lock, finds a mismatch,
    and discards the stale B-signal without touching any counter.
    """
    log_records: list[str] = []

    def record_log(msg: object) -> None:
        log_records.append(str(msg))

    sink_id = logger.add(record_log, level="DEBUG")

    producer_entered = threading.Event()
    release_producer = threading.Event()

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

    try:
        assert producer_entered.wait(timeout=2.0) is True

        # While the producer is blocked OFF the lock, the main thread
        # calls reset_tool_registry. The reset bumps _bridge_generation
        # BEFORE any other step and swaps self._inner. The post-respawn
        # alias verify makes an HTTP request to a non-existent endpoint
        # and raises McpServerError; the generation bump has already
        # happened, which is what the discard check needs.
        initial_generation = bridge.diagnostics().generation
        with pytest.raises(lifecycle.McpServerError):
            bridge.reset_tool_registry()
        assert bridge.diagnostics().generation == initial_generation + 1

        restart_count_after_reset = bridge.restart_count

        release_producer.set()
        supervisor_thread.join(timeout=2.0)
        assert not supervisor_thread.is_alive()

        # The supervisor's stale probe result was discarded: no counter
        # was advanced by the supervisor's call. The deferred counter
        # stays at zero (it would be 1 if the stale B-signal had been
        # applied to the new inner).
        assert bridge.restart_count == restart_count_after_reset
        assert bridge.diagnostics().deferred_probe_failures == 0
        assert bridge.diagnostics().consecutive_probe_failures == 0

        # The discard debug line was logged.
        assert any("stale probe result discarded" in record for record in log_records), (
            f"Expected discard debug line in {log_records!r}"
        )
    finally:
        logger.remove(sink_id)


def test_sustained_contention_probe_timeout_escalation_and_ceiling() -> None:
    """Under sustained contention, probe timeout doubles up to ceiling below preflight budget."""
    observed_timeouts: list[float] = []

    def recording_probe(endpoint: str, timeout: timedelta) -> None:
        observed_timeouts.append(timeout.total_seconds())
        raise RuntimeError("probe failed")

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=recording_probe,
        probe_timeout_fn=lambda: timedelta(seconds=1.0),
        preflight_timeout_fn=lambda: timedelta(seconds=6.0),
        contention_signal_producer=lambda: True,
    )

    # Base timeout: 1.0s, preflight: 6.0s -> ceiling: max(1.0, 6.0 - 1.0) = 5.0s
    # Call 1: deferred=0 -> timeout=1.0s
    # Call 2: deferred=1 -> timeout=min(2.0s, 5.0s) = 2.0s
    # Call 3: deferred=2 -> timeout=min(4.0s, 5.0s) = 4.0s
    # Call 4: deferred=3 -> timeout=min(8.0s, 5.0s) = 5.0s (capped at ceiling)
    # Call 5: deferred=4 -> timeout=min(16.0s, 5.0s) = 5.0s (capped at ceiling)
    for _ in range(5):
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is False

    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 5
    assert observed_timeouts == [1.0, 2.0, 4.0, 5.0, 5.0]


def test_sustained_contention_writes_durable_sink_records(tmp_path: pathlib.Path) -> None:
    """Each contention deferral appends a record with reason, duration, deferral_count, stage."""
    sink_path = configure_bridge_sink(tmp_path, "contention-bridge-test")

    def failing_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=failing_probe,
        contention_signal_producer=lambda: True,
    )

    for _ in range(3):
        bridge.check_health_and_restart_if_needed()

    records = read_durable_sink(path=sink_path)
    assert len(records) == 3

    for idx, record in enumerate(records, start=1):
        assert record["reason"] == "probe_deferred_contention"
        assert record["stage"] == "probe_deferred_contention"
        assert record["deferral_count"] == idx
        assert isinstance(record["probe_duration_s"], (float, int))
        assert record["probe_duration_s"] >= 0.0


def test_sustained_contention_ceiling_trips_replacement_at_30() -> None:
    """Sustained contention never overflows AND the deferral ceiling trips replacement.

    Regression for D-04 (no OverflowError from ``timedelta * 2**N``) and
    for the wt-16 plan's _DEFERRAL_COUNT_CEILING invariant (C3): a live
    child kept under sustained host contention must defer probes BUT
    must also be replaced once the ceiling is reached, otherwise
    indefinite deferral permanently suppresses a needed replacement.

    Before the ceiling (probes 1..29): no exception, no restart,
    ``_consecutive_probe_failures_deferred`` advances.
    After the ceiling (probe 31+): a replacement is forced, and the
    test re-enters the deferral cycle from zero.

    Every observed probe timeout must be bounded by the documented
    ceiling (max(base_timeout, preflight_timeout - 1s)).
    """
    from ralph.mcp.server.lifecycle import _DEFERRAL_COUNT_CEILING

    observed_timeouts: list[float] = []

    def always_fail_probe(endpoint: str, timeout: timedelta) -> None:
        observed_timeouts.append(timeout.total_seconds())
        raise RuntimeError("probe failed")

    inner = _make_inner(poll_result=None)
    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=lambda: _make_inner(poll_result=None),
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-sustained-60",
        probe_fn=always_fail_probe,
        probe_timeout_fn=lambda: timedelta(seconds=5.0),
        preflight_timeout_fn=lambda: timedelta(seconds=30.0),
        contention_signal_producer=lambda: True,
    )

    # Probe _DEFERRAL_COUNT_CEILING times under sustained fresh contention:
    # deferred every call, no restart, no exception. The deferred counter
    # advances from 0 to _DEFERRAL_COUNT_CEILING inclusive (the trip point).
    for _ in range(_DEFERRAL_COUNT_CEILING):
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is False
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == _DEFERRAL_COUNT_CEILING

    # The next probe trips the ceiling: a replacement is forced.
    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count >= 1

    # Every observed probe timeout must be bounded by the ceiling
    # (preflight - 1s = 29s; with base_timeout=5s the ceiling is max(5, 29) = 29s).
    assert observed_timeouts, "probe was never called"
    assert all(0.0 < t <= 29.0 for t in observed_timeouts), (
        f"observed probe timeout out of bounds: {observed_timeouts}"
    )

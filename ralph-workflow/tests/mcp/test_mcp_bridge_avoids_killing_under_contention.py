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

    def terminate(self, grace_period_s: float = 5.0, **_kwargs: object) -> None:
        self._poll_result = 0

    def wait(self, timeout: float | None = None) -> int:
        return 0

    def kill(self, **_kwargs: object) -> None:
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


def test_no_signal_regular_three_miss_policy_still_fires() -> None:
    def always_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = _make_bridge(always_fail_probe, contention_producer=lambda: False)
    bridge.check_health_and_restart_if_needed()
    bridge.check_health_and_restart_if_needed()
    bridge.check_health_and_restart_if_needed()
    bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 1


def test_w_only_defers_restart() -> None:
    record_host_contention_signal("watch_capacity_predicted")

    def always_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = _make_bridge(always_fail_probe, contention_producer=lambda: False)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 3
    for _ in range(5):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 8


def test_b_only_defers_restart() -> None:
    def always_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = _make_bridge(always_fail_probe, contention_producer=lambda: True)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 3
    for _ in range(5):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 8


def test_both_defers_restart() -> None:
    record_host_contention_signal("watch_capacity_predicted")

    def always_fail_probe(endpoint: str, timeout: timedelta) -> None:
        raise RuntimeError("probe failed")

    bridge = _make_bridge(always_fail_probe, contention_producer=lambda: True)
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    for _ in range(5):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 8


def test_successful_recovery_preserves_server() -> None:
    record_host_contention_signal("watch_capacity_predicted")
    should_fail = [True]
    contended = [True]

    def controllable_probe(endpoint: str, timeout: timedelta) -> None:
        if should_fail[0]:
            raise RuntimeError("probe failed")

    bridge = _make_bridge(controllable_probe, contention_producer=lambda: contended[0])
    for _ in range(3):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 3

    should_fail[0] = False
    bridge.check_health_and_restart_if_needed()
    assert bridge.diagnostics().deferred_probe_failures == 3
    bridge.check_health_and_restart_if_needed()
    assert bridge.diagnostics().deferred_probe_failures == 3
    bridge.check_health_and_restart_if_needed()
    assert bridge.diagnostics().consecutive_probe_failures == 0
    assert bridge.diagnostics().deferred_probe_failures == 0
    assert bridge.restart_count == 0

    should_fail[0] = True
    for _ in range(5):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 5

    clear_host_contention_signals()
    contended[0] = False
    for _ in range(2):
        bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 0
    bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 1
    assert bridge.diagnostics().deferred_probe_failures == 0


def test_process_exit_not_gated_by_contention() -> None:
    record_host_contention_signal("watch_capacity_predicted")
    inner = _make_inner(poll_result=1)

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        return _make_inner(poll_result=1)

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=McpRestartPolicy(max_restarts=10, cooldown_seconds=0.0),
        run_id="test-run",
        probe_fn=lambda ep, to: None,
        contention_signal_producer=lambda: True,
    )
    bridge.check_health_and_restart_if_needed()
    assert bridge.restart_count == 1


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


def test_stale_probe_result_discarded_after_reset() -> None:
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

        initial_generation = bridge.diagnostics().generation
        with pytest.raises(lifecycle.McpServerError):
            bridge.reset_tool_registry()
        assert bridge.diagnostics().generation == initial_generation + 1

        restart_count_after_reset = bridge.restart_count

        release_producer.set()
        supervisor_thread.join(timeout=2.0)
        assert not supervisor_thread.is_alive()

        assert bridge.restart_count == restart_count_after_reset
        assert bridge.diagnostics().deferred_probe_failures == 0
        assert bridge.diagnostics().consecutive_probe_failures == 0

        assert any("stale probe result discarded" in record for record in log_records), (
            f"Expected discard debug line in {log_records!r}"
        )
    finally:
        logger.remove(sink_id)


def test_sustained_contention_probe_timeout_escalation_and_ceiling() -> None:
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

    for _ in range(5):
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is False

    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == 5
    assert observed_timeouts == [1.0, 2.0, 4.0, 5.0, 5.0]


def test_sustained_contention_writes_durable_sink_records(tmp_path: pathlib.Path) -> None:
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

    for _ in range(_DEFERRAL_COUNT_CEILING):
        restarted = bridge.check_health_and_restart_if_needed()
        assert restarted is False
    assert bridge.restart_count == 0
    assert bridge.diagnostics().deferred_probe_failures == _DEFERRAL_COUNT_CEILING

    restarted = bridge.check_health_and_restart_if_needed()
    assert restarted is True
    assert bridge.restart_count >= 1

    assert observed_timeouts, "probe was never called"
    assert all(0.0 < t <= 29.0 for t in observed_timeouts), (
        f"observed probe timeout out of bounds: {observed_timeouts}"
    )

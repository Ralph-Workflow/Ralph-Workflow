from __future__ import annotations

import pathlib
import tempfile
from collections.abc import Generator
from datetime import timedelta

import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_attempt_result import RestartAttemptResult
from ralph.mcp.server._mcp_health_event import (
    _clear_host_contention_signals,
    pop_recent_host_contention_signals,
    record_host_contention_signal,
)
from ralph.process.mcp_supervisor import McpSupervisor, record_supervisor_tick_lateness


class FakeProcess:
    def __init__(self, poll_result: int | None = None) -> None:
        self._poll_result = poll_result
        self.terminated = False

    @property
    def pid(self) -> int:
        return 99999

    def poll(self) -> int | None:
        return self._poll_result

    def terminate(self, grace_period_s: float = 5.0) -> None:
        self.terminated = True

    def wait(self, timeout: float | None = None) -> int | None:
        return 0

    def kill(self) -> None:
        pass


class _FakeClock:
    def __init__(self, start: float = 100.0) -> None:
        self.now = start

    def __call__(self) -> float:
        return self.now

    def advance(self, seconds: float) -> None:
        self.now += seconds


@pytest.fixture(autouse=True)
def _isolate_contention_signals() -> Generator[None, None, None]:
    _clear_host_contention_signals()
    yield
    _clear_host_contention_signals()


def _make_bridge(
    *, poll_result: int | None = None
) -> tuple[lifecycle.RestartAwareMcpBridge, FakeProcess]:
    tmp_dir = pathlib.Path(tempfile.mkdtemp())
    session_file = tmp_dir / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    proc = FakeProcess(poll_result=poll_result)
    inner = lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:9500/mcp",
        process=proc,
        session_file=session_file,
    )

    def restart_fn() -> lifecycle.StandaloneMcpProcess:
        new_session = tmp_dir / "session-restart.json"
        new_session.write_text("{}", encoding="utf-8")
        return lifecycle.StandaloneMcpProcess(
            endpoint="http://127.0.0.1:9500/mcp",
            process=FakeProcess(poll_result=None),
            session_file=new_session,
        )

    bridge = lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3, cooldown_seconds=0.0),
        run_id="test-tick-lateness",
    )
    return bridge, proc


def test_tick_lateness_records_contention_signal_when_interval_exceeded() -> None:
    bridge, _ = _make_bridge()
    clock = _FakeClock(start=100.0)
    supervisor = McpSupervisor(
        bridge,
        check_interval=timedelta(seconds=2),
        _now=clock,
    )

    supervisor._do_check_once()
    sigs = pop_recent_host_contention_signals(60.0, _now=clock)
    assert len(sigs) == 0

    clock.advance(3.0)
    supervisor._do_check_once()
    sigs = pop_recent_host_contention_signals(60.0, _now=clock)
    assert len(sigs) == 0

    clock.advance(5.0)
    supervisor._do_check_once()
    sigs = pop_recent_host_contention_signals(60.0, _now=clock)
    assert len(sigs) == 1
    assert sigs[0][1] == "supervisor_tick_lateness"


def test_dead_child_bypass_triggers_immediate_restart_under_contention() -> None:
    bridge, proc = _make_bridge()
    clock = _FakeClock(start=100.0)
    supervisor = McpSupervisor(
        bridge,
        check_interval=timedelta(seconds=2),
        _now=clock,
    )

    record_host_contention_signal("watch_capacity_predicted", _now=clock)

    proc._poll_result = 1

    restarted = supervisor._do_check_once()
    assert restarted is True
    assert bridge.restart_count == 1


def test_supervisor_exit_only_raises_on_budget_exhausted() -> None:
    bridge, _ = _make_bridge()

    supervisor1 = McpSupervisor(bridge, check_interval=timedelta(milliseconds=50))
    supervisor1._mcp_error = lifecycle.McpServerError(
        "transient spawn failure",
        restart_count=1,
        failure_kind=RestartAttemptResult.ATTEMPT_FAILED,
    )
    with supervisor1:
        pass

    supervisor2 = McpSupervisor(bridge, check_interval=timedelta(milliseconds=50))
    supervisor2._mcp_error = lifecycle.McpServerError(
        "budget exhausted",
        restart_count=3,
        failure_kind=RestartAttemptResult.BUDGET_EXHAUSTED,
    )
    with pytest.raises(lifecycle.McpServerError) as exc_info, supervisor2:
        pass
    assert exc_info.value.failure_kind == RestartAttemptResult.BUDGET_EXHAUSTED


def test_record_supervisor_tick_lateness_helper() -> None:
    clock = _FakeClock(start=50.0)

    record_supervisor_tick_lateness(50.0, _now=clock)
    sigs = pop_recent_host_contention_signals(60.0, _now=clock)
    assert len(sigs) == 1
    assert sigs[0][1] == "supervisor_tick_lateness"

    _clear_host_contention_signals()

    record_supervisor_tick_lateness(
        50.0,
        check_interval=timedelta(seconds=5),
        _now=lambda: 62.0,
    )
    sigs = pop_recent_host_contention_signals(60.0, _now=lambda: 62.0)
    assert len(sigs) == 1
    assert sigs[0][1] == "supervisor_tick_lateness"

    _clear_host_contention_signals()

    record_supervisor_tick_lateness(
        50.0,
        check_interval=timedelta(seconds=5),
        _now=lambda: 58.0,
    )
    sigs = pop_recent_host_contention_signals(60.0, _now=lambda: 58.0)
    assert len(sigs) == 0

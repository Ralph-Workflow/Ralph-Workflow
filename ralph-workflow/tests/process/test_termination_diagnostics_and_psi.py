"""Tests for supervisor PSI pressure recording and termination failure diagnostics.

Covers:
- McpSupervisor calls record_psi_pressure() once per supervisor tick (exception-safe).
- ProcessManager logs "still alive after kill" / "termination failed" with psutil status
  and command name, handling NoSuchProcess and AccessDenied safely.
- Unkillable D-state child retirement records host contention signal and never blocks.
"""

from __future__ import annotations

import itertools
import sys
from typing import TYPE_CHECKING
from unittest import mock

import pytest
from loguru import logger

from ralph.mcp.server.lifecycle import RestartAwareMcpBridge
from ralph.process.manager import (
    ProcessManager,
    ProcessManagerPolicy,
    ProcessStatus,
    ProcessTerminationError,
)
from ralph.process.mcp_supervisor import McpSupervisor
from ralph.testing.fake_process import (
    FakePsutil,
    FakePsutilProcess,
    make_async_process_factory,
    make_sync_process_factory,
)

if TYPE_CHECKING:
    from collections.abc import Sequence

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
    enable_zombie_reaper=False,
)


class _StubbornDStateProcess(FakePsutilProcess):
    """Stubborn psutil process simulating uninterruptible D-state (disk-sleep)."""

    def __init__(self, pid: int, *, cmdline: Sequence[str] = ("git", "status")) -> None:
        super().__init__(pid=pid, stubborn=True, _status="disk-sleep")
        self._custom_cmdline = list(cmdline)

    def status(self) -> str:
        return "disk-sleep"

    def cmdline(self) -> list[str]:
        return self._custom_cmdline

    def name(self) -> str:
        return "git"

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass


class _FakeBridge(RestartAwareMcpBridge):
    """Minimal test bridge for McpSupervisor."""

    def __init__(self) -> None:
        self._restart_count = 0
        self.health_check_called = 0

    @property
    def run_id(self) -> str:
        return "test-run-123"

    @property
    def restart_count(self) -> int:
        return self._restart_count

    def check_health_and_restart_if_needed(self) -> bool:
        self.health_check_called += 1
        return False


def test_supervisor_tick_calls_record_psi_pressure() -> None:
    """McpSupervisor.tick calls record_psi_pressure once per tick."""
    bridge = _FakeBridge()
    psi_calls: list[int] = []

    def fake_record_psi() -> None:
        psi_calls.append(1)

    supervisor = McpSupervisor(
        bridge,
        _record_psi_pressure=fake_record_psi,
    )

    restarted = supervisor.tick()
    assert not restarted
    assert len(psi_calls) == 1
    assert bridge.health_check_called == 1


def test_supervisor_tick_record_psi_pressure_exception_safe() -> None:
    """McpSupervisor.tick silently swallows exceptions from record_psi_pressure."""
    bridge = _FakeBridge()

    def failing_record_psi() -> None:
        raise OSError("Virtual /proc/pressure/cpu vanished")

    supervisor = McpSupervisor(
        bridge,
        _record_psi_pressure=failing_record_psi,
    )

    # Must not raise despite failing record_psi_pressure
    restarted = supervisor.tick()
    assert not restarted
    assert bridge.health_check_called == 1


def test_termination_diagnostics_include_psutil_status_and_command_on_alive_after_kill() -> None:
    """Termination failure logs include psutil status and command name."""
    sync_factory = make_sync_process_factory(itertools.count(100), returncode=None)
    fake_psutil = FakePsutil()
    stubborn_proc = _StubbornDStateProcess(pid=100, cmdline=("git", "status"))
    fake_psutil._processes = {100: stubborn_proc}

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(200)),
        psutil=fake_psutil,
    )

    logs: list[str] = []
    sink_id = logger.add(lambda msg: logs.append(str(msg)), level="ERROR")
    try:
        handle = pm.spawn(["git", "status"])
        with pytest.raises(ProcessTerminationError):
            handle.terminate(grace_period_s=0.0)

        # Check that "still alive after kill" contains status and command
        still_alive_logs = [log for log in logs if "still alive after kill" in log]
        assert len(still_alive_logs) >= 1
        assert "status=disk-sleep" in still_alive_logs[0]
        assert "command=git status" in still_alive_logs[0]

        # Check that "termination failed" contains status and command
        term_failed_logs = [log for log in logs if "termination failed" in log]
        assert len(term_failed_logs) >= 1
        assert "status=disk-sleep" in term_failed_logs[0]
        assert "command=git status" in term_failed_logs[0]
    finally:
        logger.remove(sink_id)


def test_termination_diagnostics_handles_no_such_process() -> None:
    """When psutil raises NoSuchProcess on status check, status is no_such_process."""
    sync_factory = make_sync_process_factory(itertools.count(100), returncode=None)

    class _NoSuchProcessFake(FakePsutilProcess):
        def status(self) -> str:
            raise FakePsutil.NoSuchProcess(self.pid)

    fake_psutil = FakePsutil()
    fake_psutil._processes = {100: _NoSuchProcessFake(pid=100)}
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(200)),
        psutil=fake_psutil,
    )

    handle = pm.spawn([sys.executable, "-m", "worker"])
    status, cmd = pm._get_process_diagnostics(handle.record.pid, record=handle.record)
    assert status == "no_such_process"
    assert cmd == f"{sys.executable} -m worker"

    logs: list[str] = []
    sink_id = logger.add(lambda msg: logs.append(str(msg)), level="ERROR")
    try:
        pm._mark_termination_failed(handle.record)
        term_failed_logs = [log for log in logs if "termination failed" in log]
        assert len(term_failed_logs) == 1
        assert "status=no_such_process" in term_failed_logs[0]
        assert f"command={sys.executable} -m worker" in term_failed_logs[0]
    finally:
        logger.remove(sink_id)


def test_termination_diagnostics_handles_access_denied() -> None:
    """When psutil raises AccessDenied on status check, status is access_denied."""
    sync_factory = make_sync_process_factory(itertools.count(100), returncode=None)

    class _AccessDeniedFake(FakePsutilProcess):
        def status(self) -> str:
            raise FakePsutil.AccessDenied(self.pid)

    fake_psutil = FakePsutil()
    fake_psutil._processes = {100: _AccessDeniedFake(pid=100)}
    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(200)),
        psutil=fake_psutil,
    )

    handle = pm.spawn(["sleep", "10"])
    status, cmd = pm._get_process_diagnostics(handle.record.pid, record=handle.record)
    assert status == "access_denied"
    assert cmd == "sleep 10"

    logs: list[str] = []
    sink_id = logger.add(lambda msg: logs.append(str(msg)), level="ERROR")
    try:
        pm._mark_termination_failed(handle.record)
        term_failed_logs = [log for log in logs if "termination failed" in log]
        assert len(term_failed_logs) == 1
        assert "status=access_denied" in term_failed_logs[0]
        assert "command=sleep 10" in term_failed_logs[0]
    finally:
        logger.remove(sink_id)


def test_d_state_retirement_failure_recorded_as_contention_and_non_blocking() -> None:
    """D-state retirement failure emits host contention signal and never blocks."""
    sync_factory = make_sync_process_factory(itertools.count(100), returncode=None)
    fake_psutil = FakePsutil()
    stubborn_proc = _StubbornDStateProcess(pid=100)
    fake_psutil._processes = {100: stubborn_proc}

    pm = ProcessManager(
        policy=_FAST_POLICY,
        sync_process_factory=sync_factory,
        async_process_factory=make_async_process_factory(itertools.count(200)),
        psutil=fake_psutil,
    )

    signals: list[str] = []

    def fake_record_signal(reason: str, *, _now: object = None) -> None:
        signals.append(reason)

    handle = pm.spawn(["git", "diff"])
    with (
        mock.patch(
            "ralph.mcp.server._mcp_health_event.record_host_contention_signal",
            side_effect=fake_record_signal,
        ),
        pytest.raises(ProcessTerminationError),
    ):
        handle.terminate(grace_period_s=0.0)

    # Contention signal assertion
    assert "d_state:disk-sleep" in signals

    # Process record state: failed with termination_failed
    assert handle.record.status == ProcessStatus.FAILED
    assert handle.record.cause == "termination_failed"
    # Not active anymore
    assert pm.list_active() == []

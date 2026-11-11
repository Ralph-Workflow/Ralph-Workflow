from __future__ import annotations

import os
import sys
from types import SimpleNamespace
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import AliveBy, TimeoutPolicy
from ralph.agents.invoke._process_reader import ProcessLineReader
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._types import ProcessReaderCtx
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    import pytest


class _FakeCpuTimes:
    def __init__(self, user: float = 0.0, system: float = 0.0) -> None:
        self.user = user
        self.system = system


class _FakeProc:
    def __init__(
        self,
        pid: int,
        *,
        children: list[_FakeProc] | None = None,
        cpu_user: float = 0.0,
        cpu_system: float = 0.0,
    ) -> None:
        self.pid = pid
        self._children = children or []
        self._cpu_user = cpu_user
        self._cpu_system = cpu_system

    def children(self, recursive: bool = True) -> list[_FakeProc]:
        del recursive
        return list(self._children)

    def cpu_times(self) -> _FakeCpuTimes:
        return _FakeCpuTimes(self._cpu_user, self._cpu_system)


def _make_fake_psutil(procs: dict[int, _FakeProc]) -> object:

    class _Psutil:
        NoSuchProcess = type("NoSuchProcess", (Exception,), {})
        AccessDenied = type("AccessDenied", (Exception,), {})
        ZombieProcess = type("ZombieProcess", (Exception,), {})

        def __init__(self, table: dict[int, _FakeProc]) -> None:
            self._table = table

        def process(self, pid: int) -> _FakeProc:
            return self._table[pid]

        def __getattr__(self, name: str) -> object:
            if name == "Process":
                return self.process
            raise AttributeError(name)

    return _Psutil(procs)


def _patch_psutil(
    monkeypatch: pytest.MonkeyPatch,
    class_ref: type,
    procs: dict[int, _FakeProc],
) -> None:
    module = sys.modules[class_ref.__module__]
    monkeypatch.setattr(module, "psutil", _make_fake_psutil(procs))


class _FakePtyHandle(SimpleNamespace):
    def __init__(self, master_fd: int) -> None:
        super().__init__(master_fd=master_fd, pid=100)

    def poll(self) -> int | None:
        return None

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        pass

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (0, None)


def _make_pty_reader() -> PtyLineReader:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakePtyHandle(master_fd)
    except OSError:
        os.close(master_fd)
        raise
    ctx = SimpleNamespace(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        policy=TimeoutPolicy(idle_timeout_seconds=300.0, cpu_idle_seconds=10.0),
        monitor=None,
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
    )
    reader = PtyLineReader(
        handle,
        "test-agent",
        ctx,
        FakeClock(start=0.0),
        extras=None,
    )
    return reader


def _make_process_reader() -> ProcessLineReader:
    class _Handle:
        def __init__(self) -> None:
            self.pid: int | None = 100
            self.stdout = iter([])

        def poll(self) -> int | None:
            return None

        def terminate(self, *, grace_period_s: float = 0.5) -> None:
            pass

    handle = _Handle()
    ctx = ProcessReaderCtx(
        config=AgentConfig(cmd="test-agent", transport=AgentTransport.GENERIC),
        policy=TimeoutPolicy(idle_timeout_seconds=300.0, cpu_idle_seconds=10.0),
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
        monitor=None,
        workspace_path=None,
    )
    return ProcessLineReader(handle, ctx, FakeClock(start=0.0))


def test_pty_line_reader_prunes_stale_pid_from_cpu_baselines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _FakeProc(101, cpu_user=1.0)
    host = _FakeProc(100, children=[child])
    _patch_psutil(monkeypatch, PtyLineReader, {100: host, 101: child})

    reader = _make_pty_reader()
    assert reader._probe_cpu_idle(scoped_active=True, alive_by=None) is False
    assert 101 in reader._cpu_baselines
    reader._cpu_baselines[999] = (0.0, 0.0)

    result = reader._probe_cpu_idle(scoped_active=True, alive_by=None)
    assert result is False
    assert 999 not in reader._cpu_baselines, (
        "stale PID (gone from child_pids) must be swept from _cpu_baselines"
    )
    assert 101 in reader._cpu_baselines, "live PID must NOT be swept"


def test_pty_line_reader_prunes_stale_pids_when_no_children_remain(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _FakeProc(101, cpu_user=1.0)
    host = _FakeProc(100, children=[child])
    _patch_psutil(monkeypatch, PtyLineReader, {100: host, 101: child})
    reader = _make_pty_reader()
    reader._probe_cpu_idle(scoped_active=True, alive_by=None)
    assert 101 in reader._cpu_baselines

    host._children = []
    reader._probe_cpu_idle(scoped_active=True, alive_by=None)
    assert 101 not in reader._cpu_baselines, (
        "stale child PID must be swept when absent from the next children() listing"
    )


def test_process_line_reader_prunes_stale_pid_from_cpu_baselines(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    child = _FakeProc(101, cpu_user=1.0)
    host = _FakeProc(100, children=[child])
    _patch_psutil(monkeypatch, ProcessLineReader, {100: host, 101: child})

    reader = _make_process_reader()
    assert reader._probe_cpu_idle(scoped_active=True, alive_by=None) is False
    assert 101 in reader._cpu_baselines
    reader._cpu_baselines[999] = (0.0, 0.0)

    result = reader._probe_cpu_idle(scoped_active=True, alive_by=None)
    assert result is False
    assert 999 not in reader._cpu_baselines, (
        "stale PID (gone from child_pids) must be swept from _cpu_baselines"
    )
    assert 101 in reader._cpu_baselines, "live PID must NOT be swept"


def test_probe_cpu_idle_returns_false_when_scoped_inactive() -> None:
    reader = _make_pty_reader()
    reader._cpu_baselines[999] = (0.0, 0.0)

    assert reader._probe_cpu_idle(scoped_active=False, alive_by=None) is False
    assert 999 in reader._cpu_baselines


def test_probe_cpu_idle_returns_false_when_alive_by_indicates_progress() -> None:
    reader = _make_pty_reader()
    reader._cpu_baselines[999] = (0.0, 0.0)

    for alive_by in (
        AliveBy.CPU_IDLE_WHILE_ALIVE,
        AliveBy.LOG_STALE_WHILE_ALIVE,
        AliveBy.FRESH_PROGRESS,
        AliveBy.FRESH_HEARTBEAT_ONLY,
        AliveBy.STALE_LABEL_ONLY,
    ):
        assert reader._probe_cpu_idle(scoped_active=True, alive_by=alive_by) is False
    assert 999 in reader._cpu_baselines

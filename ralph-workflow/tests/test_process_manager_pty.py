from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any, cast

import pytest

from ralph.process import ProcessManager, ProcessManagerPolicy, ProcessStatus, PtySpawnOptions
from ralph.process import pty as pty_module
from ralph.testing.fake_process import FakePsutil

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path


_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.1,
    kill_followup_timeout_s=0.1,
    log_events=False,
)
_PTY_COLUMNS = 80
_PTY_ROWS = 24
_KILLED_RETURN_CODE = -9
_ROOT_ONLY_WAIT_CALLS = 2


@dataclass
class _FakePtyProcess:
    pid: int
    master_fd: int
    slave_fd: int
    returncode: int | None = None
    terminated: bool = False
    killed: bool = False
    closed: bool = False

    def poll(self) -> int | None:
        return self.returncode

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        return self.returncode if self.returncode is not None else 0

    def terminate(self) -> None:
        self.terminated = True
        if self.returncode is None:
            self.returncode = -15

    def kill(self) -> None:
        self.killed = True
        self.returncode = -9

    def close(self) -> None:
        self.closed = True

    def fileno(self) -> int:
        return self.master_fd

    def isatty(self) -> bool:
        return True


class _FakePtyFactory:
    def __init__(self) -> None:
        self.calls: list[tuple[tuple[str, ...], str | None, dict[str, str] | None]] = []
        self._pids = itertools.count(2000)

    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        cols: int,
        rows: int,
    ) -> _FakePtyProcess:
        assert cols == _PTY_COLUMNS
        assert rows == _PTY_ROWS
        self.calls.append((tuple(command), cwd, env))
        pid = next(self._pids)
        return _FakePtyProcess(pid=pid, master_fd=pid + 10, slave_fd=pid + 11)


class _TimeoutThenKillPtyProcess(_FakePtyProcess):
    wait_calls: int = 0

    def wait(self, timeout: float | None = None) -> int:
        del timeout
        self.wait_calls += 1
        if self.killed:
            return self.returncode if self.returncode is not None else -9
        raise TimeoutError


class _TimeoutThenKillPtyFactory(_FakePtyFactory):
    def __call__(
        self,
        command: Sequence[str],
        *,
        cwd: str | None,
        env: dict[str, str] | None,
        cols: int,
        rows: int,
    ) -> _TimeoutThenKillPtyProcess:
        assert cols == _PTY_COLUMNS
        assert rows == _PTY_ROWS
        self.calls.append((tuple(command), cwd, env))
        pid = next(self._pids)
        return _TimeoutThenKillPtyProcess(pid=pid, master_fd=pid + 10, slave_fd=pid + 11)


def test_spawn_pty_records_pid_pgid_and_terminal_status(tmp_path: Path) -> None:
    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=cast("Any", FakePsutil()),
    )

    handle = pm.spawn_pty(
        ["claude", "PROMPT.md"],
        PtySpawnOptions(
            cwd=str(tmp_path), env={"TERM": "xterm-256color"}, label="invoke:claude-interactive"
        ),
    )

    assert factory.calls == [(("claude", "PROMPT.md"), str(tmp_path), {"TERM": "xterm-256color"})]
    assert handle.record.status == ProcessStatus.RUNNING
    assert handle.record.pid == handle.pid
    assert handle.record.pgid == handle.pid
    assert handle.master_fd == handle.pid + 10
    assert handle.isatty() is True


def test_managed_pty_process_exposes_master_read_handle(tmp_path: Path) -> None:
    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=cast("Any", FakePsutil()),
    )

    handle = pm.spawn_pty(["claude", "PROMPT.md"], PtySpawnOptions(cwd=str(tmp_path)))

    assert handle.master_fd == handle.pid + 10
    assert handle.fileno() == handle.master_fd


def test_terminate_pty_process_kills_process_group(tmp_path: Path) -> None:
    factory = _FakePtyFactory()
    psutil_mod = FakePsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=cast("Any", psutil_mod),
    )

    handle = pm.spawn_pty(
        ["claude", "PROMPT.md"], PtySpawnOptions(cwd=str(tmp_path), label="invoke:claude")
    )
    psutil_proc = psutil_mod.process_from_pid(handle.pid)
    child = psutil_mod.process_from_pid(handle.pid + 1)
    psutil_proc._children = [child]

    handle.terminate(grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.KILLED
    assert psutil_proc._terminated or psutil_proc._killed
    assert child._terminated or child._killed


def test_terminate_pty_process_root_only_handles_builtin_timeout(tmp_path: Path) -> None:
    factory = _TimeoutThenKillPtyFactory()
    pm = ProcessManager(policy=_FAST_POLICY, pty_process_factory=factory, psutil=None)

    handle = pm.spawn_pty(["claude", "PROMPT.md"], cwd=str(tmp_path), label="invoke:claude")
    proc = cast("_TimeoutThenKillPtyProcess", handle._proc)

    handle.terminate(grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.KILLED
    assert proc.terminated is True
    assert proc.killed is True
    assert proc.closed is True
    assert proc.wait_calls == _ROOT_ONLY_WAIT_CALLS
    assert handle.record.returncode == _KILLED_RETURN_CODE


def test_spawn_pty_process_rejects_non_posix_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pty_module.os, "name", "nt")

    with pytest.raises(OSError, match="POSIX"):
        pty_module.spawn_pty_process(["python"], cwd=None, env=None)

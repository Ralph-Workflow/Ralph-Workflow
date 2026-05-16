from __future__ import annotations

import itertools
from dataclasses import dataclass
from typing import TYPE_CHECKING

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
        opts: PtySpawnOptions,
    ) -> _FakePtyProcess:
        assert opts.cols == _PTY_COLUMNS
        assert opts.rows == _PTY_ROWS
        self.calls.append((tuple(command), opts.cwd, opts.env))
        pid = next(self._pids)
        return _FakePtyProcess(pid=pid, master_fd=pid + 10, slave_fd=pid + 11)


def test_spawn_pty_records_pid_pgid_and_terminal_status(tmp_path: Path) -> None:
    factory = _FakePtyFactory()
    pm = ProcessManager(policy=_FAST_POLICY, pty_process_factory=factory, psutil=FakePsutil())

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
    pm = ProcessManager(policy=_FAST_POLICY, pty_process_factory=factory, psutil=FakePsutil())

    handle = pm.spawn_pty(["claude", "PROMPT.md"], PtySpawnOptions(cwd=str(tmp_path)))

    assert handle.master_fd == handle.pid + 10
    assert handle.fileno() == handle.master_fd


def test_terminate_pty_process_kills_process_group(tmp_path: Path) -> None:
    factory = _FakePtyFactory()
    psutil_mod = FakePsutil()
    pm = ProcessManager(policy=_FAST_POLICY, pty_process_factory=factory, psutil=psutil_mod)

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


def test_spawn_pty_process_rejects_non_posix_platform(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(pty_module.os, "name", "nt")

    with pytest.raises(OSError, match="POSIX"):
        pty_module.spawn_pty_process(["python"], cwd=None, env=None)

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

import pytest

from ralph.process import ProcessManager, ProcessManagerPolicy, ProcessStatus, PtySpawnOptions
from ralph.process import pty as pty_module
from ralph.testing.fake_process import FakePsutil

if TYPE_CHECKING:
    from pathlib import Path

    from tests.test_process_manager_pty_helper__timeoutthenkillptyprocess import (
        _TimeoutThenKillPtyProcess,
    )
from tests.test_process_manager_pty_helper__fakeptyfactory import _FakePtyFactory
from tests.test_process_manager_pty_helper__timeoutthenkillptyfactory import (
    _TimeoutThenKillPtyFactory,
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.1,
    kill_followup_timeout_s=0.1,
    log_events=False,
)
_PTY_COLUMNS = 80
_PTY_ROWS = 24
_KILLED_RETURN_CODE = -9
_ROOT_ONLY_WAIT_CALLS = 2


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

    # Mock os.kill to succeed so the liveness check returns ALIVE
    with patch("os.kill", return_value=None):
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

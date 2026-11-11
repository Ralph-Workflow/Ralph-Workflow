from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

from ralph.process import ProcessManager, ProcessManagerPolicy, ProcessStatus, PtySpawnOptions
from ralph.process import pty as pty_module
from ralph.testing.fake_process import FakePsutil, FakePsutilProcess
from tests._process_manager_pty_helper__fakeprocess import _FakePtyProcess

if TYPE_CHECKING:
    from pathlib import Path

from tests._process_manager_pty_helper__fakeptyfactory import _FakePtyFactory
from tests._process_manager_pty_helper__timeoutthenkillptyfactory import (
    _TimeoutThenKillPtyFactory,
)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.1,
    kill_followup_timeout_s=0.1,
    log_events=False,
    enable_zombie_reaper=False,
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
        psutil=FakePsutil(),
    )

    handle = pm.spawn_pty(
        ["claude", "PROMPT.md"],
        PtySpawnOptions(
            cwd=str(tmp_path), env={"TERM": "xterm-256color"}, label="invoke:claude-interactive"
        ),
    )

    assert factory.calls == [
        (
            ("claude", "PROMPT.md"),
            str(tmp_path),
            {"TERM": "xterm-256color", "PWD": str(tmp_path)},
        )
    ]
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
        psutil=FakePsutil(),
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
        psutil=psutil_mod,
    )

    handle = pm.spawn_pty(
        ["claude", "PROMPT.md"], PtySpawnOptions(cwd=str(tmp_path), label="invoke:claude")
    )
    psutil_proc = psutil_mod.process_from_pid(handle.pid)
    child = psutil_mod.process_from_pid(handle.pid + 1)
    psutil_proc._children = [child]

    handle.terminate(grace_period_s=0.0)

    assert handle.record.status == ProcessStatus.KILLED
    outcomes = pm.list_termination_outcomes()[handle.pid]
    stages = [outcome["stage"] for outcome in outcomes]
    assert stages.index("terminal_reason") < stages.index("graceful_terminate")
    assert outcomes[stages.index("terminal_reason")]["outcome"] == "operator_cancellation"
    assert psutil_proc._terminated or psutil_proc._killed
    assert child._terminated or child._killed


def test_terminate_pty_process_root_only_handles_builtin_timeout(tmp_path: Path) -> None:
    factory = _TimeoutThenKillPtyFactory()
    pm = ProcessManager(policy=_FAST_POLICY, pty_process_factory=factory, psutil=None)
    handle = pm.spawn_pty(["claude", "PROMPT.md"], cwd=str(tmp_path), label="invoke:claude")
    proc = handle._proc

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


def test_spawn_pty_factory_raises_oserror_records_failed(tmp_path: Path) -> None:

    def raising_factory(command: object, **kwargs: object) -> object:
        del command, kwargs
        raise OSError("pty factory failure")

    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=raising_factory,
        psutil=None,
    )
    with pytest.raises(OSError, match="pty factory failure"):
        pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    assert pm.list_active() == []


def test_managed_pty_wait_raises_on_live_process(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=None,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = handle._proc

    class _RaisingPty(_FakePtyProcess):
        def wait(self, timeout: float | None = None) -> int:
            del timeout
            raise TimeoutError("fake-pty-timeout")

    handle._proc = _RaisingPty(pid=proc.pid, master_fd=proc.master_fd, slave_fd=proc.slave_fd)
    with pytest.raises(TimeoutError):
        handle.wait(timeout=0.001)


def test_managed_pty_wait_marks_exited_on_returncode(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=None,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = handle._proc
    proc.returncode = 0

    rc = handle.wait(timeout=0.1)

    assert rc == 0
    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.cause == "exited"


def test_managed_pty_poll_returns_none_when_live(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=None,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = handle._proc
    proc.returncode = None

    assert handle.poll() is None
    assert handle.record.status == ProcessStatus.RUNNING


def test_managed_pty_poll_marks_exited_when_returncode(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=None,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = handle._proc
    proc.returncode = 0

    assert handle.poll() == 0
    assert handle.record.status == ProcessStatus.EXITED


def test_managed_pty_terminate_calls_escalate_with_default_grace(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    psutil_mod = FakePsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=psutil_mod,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = psutil_mod.process_from_pid(handle.pid)
    proc._children = []

    handle.terminate()

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


def test_managed_pty_terminate_uses_overridden_grace_period(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    psutil_mod = FakePsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=psutil_mod,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = psutil_mod.process_from_pid(handle.pid)
    proc._children = []

    handle.terminate(grace_period_s=0.5)

    assert handle.record.status == ProcessStatus.KILLED


def test_managed_pty_terminate_is_noop_when_already_terminal(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=FakePsutil(),
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = handle._proc
    proc.returncode = 0
    handle.wait(timeout=0.1)
    handle.terminate(grace_period_s=0.0)
    assert handle.record.status == ProcessStatus.EXITED
    assert handle.record.cause == "exited"


def test_managed_pty_kill_uses_zero_grace_period(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    psutil_mod = FakePsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=psutil_mod,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = psutil_mod.process_from_pid(handle.pid)
    proc._children = []

    handle.kill()

    assert handle.record.status == ProcessStatus.KILLED


def test_managed_pty_has_live_descendants_returns_false_without_psutil(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=None,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = handle._proc
    proc._terminated = False
    proc._killed = False
    assert handle.has_live_descendants() is False


def test_managed_pty_has_live_descendants_with_psutil(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    psutil_mod = FakePsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=psutil_mod,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = psutil_mod.process_from_pid(handle.pid)
    live_child = FakePsutilProcess(pid=handle.pid + 1, _running=True, _status="sleeping")
    proc._children = [live_child]
    assert handle.has_live_descendants() is True


def test_managed_pty_descendant_snapshot_without_psutil_returns_zero(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=None,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = handle._proc
    proc._terminated = False
    proc._killed = False
    count, oldest = handle.descendant_snapshot()
    assert count == 0
    assert oldest is None


def test_managed_pty_descendant_snapshot_with_psutil_includes_only_live(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    psutil_mod = FakePsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=psutil_mod,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = psutil_mod.process_from_pid(handle.pid)
    live_child = FakePsutilProcess(pid=handle.pid + 1, _running=True, _status="sleeping")
    zombie_child = FakePsutilProcess(pid=handle.pid + 2, _running=True, _status="zombie")
    proc._children = [live_child, zombie_child]

    count, oldest = handle.descendant_snapshot()

    assert count == 1
    assert oldest is not None


def test_managed_pty_close_calls_proc_close(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=None,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = handle._proc
    assert proc.closed is False
    handle.close()
    assert proc.closed is True


def test_managed_pty_context_manager_terminates_when_live(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    psutil_mod = FakePsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=psutil_mod,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = psutil_mod.process_from_pid(handle.pid)
    proc._children = []

    with handle:
        pass

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


def test_managed_pty_context_manager_closes_only_on_keyboard_interrupt(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=None,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = handle._proc
    proc.returncode = None

    with pytest.raises(KeyboardInterrupt), handle:
        raise KeyboardInterrupt

    assert proc.closed is True
    assert handle.record.status == ProcessStatus.RUNNING


def test_managed_pty_context_manager_waits_after_terminate(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    psutil_mod = FakePsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=psutil_mod,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = psutil_mod.process_from_pid(handle.pid)
    proc._children = []

    with handle:
        pass

    assert handle.record.status == ProcessStatus.KILLED
    assert handle.record.cause == "killed"


def test_managed_pty_context_manager_does_not_wait_when_already_terminal(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    psutil_mod = FakePsutil()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=psutil_mod,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = handle._proc
    proc.returncode = 0
    handle.wait(timeout=0.1)
    assert handle.record.status == ProcessStatus.EXITED

    with handle:
        pass

    assert handle.record.status == ProcessStatus.EXITED


def test_managed_pty_master_fd_returns_proc_master_fd(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=None,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = handle._proc
    assert handle.master_fd == proc.master_fd


def test_managed_pty_slave_fd_returns_proc_slave_fd(tmp_path: Path) -> None:

    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=None,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    proc = handle._proc
    assert handle.slave_fd == proc.slave_fd


def test_managed_pty_isatty_returns_true(tmp_path: Path) -> None:
    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=None,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    assert handle.isatty() is True


def test_managed_pty_fileno_returns_master_fd(tmp_path: Path) -> None:
    factory = _FakePtyFactory()
    pm = ProcessManager(
        policy=_FAST_POLICY,
        pty_process_factory=factory,
        psutil=None,
    )
    handle = pm.spawn_pty(["claude"], PtySpawnOptions(cwd=str(tmp_path)))
    assert handle.fileno() == handle.master_fd

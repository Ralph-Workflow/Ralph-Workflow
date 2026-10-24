from __future__ import annotations

from collections.abc import Callable
from datetime import UTC, datetime
from typing import Any

import pytest

from ralph.process.manager import (
    ManagedPtyProcess,
    ProcessManager,
    ProcessManagerPolicy,
    ProcessRecord,
    ProcessStatus,
)
from ralph.process.pty import PtyProcess

pytestmark = pytest.mark.timeout_seconds(5)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
    enable_zombie_reaper=False,
)


class _StubPtyProc:
    """Stub satisfying _PtyProcessLike protocol."""

    pid = 99999
    master_fd = -1
    slave_fd = -1

    def __init__(self, returncode: int | None = None) -> None:
        self._rc = returncode

    @property
    def returncode(self) -> int | None:
        return self._rc

    def poll(self) -> int | None:
        return self._rc

    def wait(self, timeout: float | None = None) -> int:
        return self._rc if self._rc is not None else 0

    def terminate(self) -> None:
        pass

    def kill(self) -> None:
        pass

    def close(self) -> None:
        pass

    def fileno(self) -> int:
        return -1

    def isatty(self) -> bool:
        return False

    def note_external_returncode(self, rc: int) -> None:
        if self._rc is None:
            self._rc = rc


def test_note_external_returncode_sets_value() -> None:
    pty = PtyProcess(pid=0, master_fd=-1, slave_fd=-1)
    pty.note_external_returncode(7)
    assert pty.returncode == 7


def test_note_external_returncode_is_idempotent() -> None:
    pty = PtyProcess(pid=0, master_fd=-1, slave_fd=-1)
    pty.note_external_returncode(7)
    pty.note_external_returncode(8)
    assert pty.returncode == 7


def test_note_external_returncode_rejects_non_int() -> None:
    pty = PtyProcess(pid=0, master_fd=-1, slave_fd=-1)
    call_external_rc: Callable[..., Any] = pty.note_external_returncode
    with pytest.raises(TypeError, match="returncode must be int"):
        call_external_rc(7.5)


def test_note_external_returncode_rejects_bool() -> None:
    pty = PtyProcess(pid=0, master_fd=-1, slave_fd=-1)
    call_external_rc: Callable[..., Any] = pty.note_external_returncode
    with pytest.raises(TypeError, match="returncode must be int"):
        call_external_rc(True)


def test_note_external_returncode_accepts_negative() -> None:
    pty = PtyProcess(pid=0, master_fd=-1, slave_fd=-1)
    pty.note_external_returncode(-15)
    assert pty.returncode == -15


def test_poll_returns_external_returncode_when_set() -> None:
    pty = PtyProcess(pid=0, master_fd=-1, slave_fd=-1)
    pty.note_external_returncode(0)
    assert pty.poll() == 0


def test_managed_pty_returncode_falls_back_to_record_when_terminal() -> None:
    record = ProcessRecord(
        pid=99999,
        pgid=99999,
        command=("test",),
        cwd=None,
        started_at=datetime.now(tz=UTC),
        status=ProcessStatus.KILLED,
        ended_at=datetime.now(tz=UTC),
        cause="killed",
        returncode=-15,
    )
    manager = ProcessManager(policy=_FAST_POLICY)
    handle = ManagedPtyProcess(_StubPtyProc(returncode=None), record, manager)
    assert handle.returncode == -15


def test_managed_pty_returncode_returns_proc_returncode_when_present() -> None:
    record = ProcessRecord(
        pid=99999,
        pgid=99999,
        command=("test",),
        cwd=None,
        started_at=datetime.now(tz=UTC),
        status=ProcessStatus.RUNNING,
    )
    manager = ProcessManager(policy=_FAST_POLICY)
    handle = ManagedPtyProcess(_StubPtyProc(returncode=0), record, manager)
    assert handle.returncode == 0


def test_managed_pty_returncode_returns_none_when_proc_none_and_running() -> None:
    record = ProcessRecord(
        pid=99999,
        pgid=99999,
        command=("test",),
        cwd=None,
        started_at=datetime.now(tz=UTC),
        status=ProcessStatus.RUNNING,
    )
    manager = ProcessManager(policy=_FAST_POLICY)
    handle = ManagedPtyProcess(_StubPtyProc(returncode=None), record, manager)
    assert handle.returncode is None


def test_managed_pty_original_terminal_reason_delegates() -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    pid = 99999
    manager.record_terminal_reason(pid, "interactive_completion", "test_issuer")
    record = ProcessRecord(
        pid=pid,
        pgid=pid,
        command=("test",),
        cwd=None,
        started_at=datetime.now(tz=UTC),
        status=ProcessStatus.RUNNING,
    )
    handle = ManagedPtyProcess(_StubPtyProc(), record, manager)
    assert handle.original_terminal_reason() == "interactive_completion"

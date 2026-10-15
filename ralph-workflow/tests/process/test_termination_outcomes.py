from __future__ import annotations

from datetime import UTC, datetime

import pytest

from ralph.process.manager import ManagedPtyProcess, ProcessManager, ProcessManagerPolicy
from ralph.process.manager._process_record import ProcessRecord
from ralph.process.manager._process_status import ProcessStatus

pytestmark = pytest.mark.timeout_seconds(5)

_FAST_POLICY = ProcessManagerPolicy(
    default_grace_period_s=0.0,
    kill_followup_timeout_s=0.0,
    log_events=False,
    enable_zombie_reaper=False,
)


class _StubPtyProc:
    """Stub satisfying _PtyProcessLike protocol: pid, master_fd, slave_fd, returncode==None."""

    pid = 99999
    master_fd = -1
    slave_fd = -1

    @property
    def returncode(self) -> int | None:
        return None

    def poll(self) -> int | None:
        return None

    def wait(self, timeout: float | None = None) -> int:
        return 0

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
        pass


def test_original_terminal_reason_returns_first_recorded() -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    pid = 99999
    manager.record_terminal_reason(pid, "interactive_completion", "agy")
    manager._record_termination_outcome(pid, "graceful_terminate", "sent")
    manager._record_termination_outcome(pid, "force_kill", "sent")
    assert manager.original_terminal_reason(pid) == "interactive_completion"
    assert manager.termination_issuer(pid) == "sent"


def test_original_terminal_reason_returns_none_when_no_record() -> None:
    manager = ProcessManager(policy=_FAST_POLICY)
    assert manager.original_terminal_reason(7777777) is None


def test_managed_pty_returncode_falls_back_to_record() -> None:
    """When _proc.returncode is None but the record is terminal with an int returncode,
    ManagedPtyProcess.returncode falls back to the record value."""
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
    handle = ManagedPtyProcess(_StubPtyProc(), record, manager)
    assert handle.returncode == -15

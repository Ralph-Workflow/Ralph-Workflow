from __future__ import annotations

import os
import pty
import threading
from typing import TYPE_CHECKING

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import AgentRunCtx, PtyLineReader
from ralph.agents.invoke.attempt_supervision import AttemptSupervisor, CompletionObserved
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.manager import ProcessTerminationIssuer

if TYPE_CHECKING:
    from pathlib import Path

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "a completed pty attempt is not reported as a broken agent": (
        "the pty reader passed no attempt supervisor to the broken agent detector, so a "
        "completion that ralph terminated with SIGTERM was reported as a broken agent with no "
        "meaningful output; the reader now passes its supervisor and the detector stands down "
        "for an attempt that already recorded a terminal observation"
    ),
}

_SIGTERM_RETURNCODE = -15
_ALIVE_POLLS = 3
_IDLE_POLL_INTERVAL_SECONDS = 0.5


class _NoopProcessTeardown:
    def teardown_subtree(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        owner_label: str = "invoke",
    ) -> None:
        del host_pid, issuer, owner_label


class _ReaderLoopExitHandle:
    pid: None = None

    def __init__(self, master_fd: int, reader_ident: int) -> None:
        self.master_fd = master_fd
        self._reader_ident = reader_ident
        self._reader_polls = 0
        self.terminate_issuers: list[ProcessTerminationIssuer] = []
        self.terminal_reasons: list[str] = []

    def poll(self) -> int | None:
        if threading.get_ident() != self._reader_ident:
            if self._reader_polls < _ALIVE_POLLS + 1:
                return None
            return _SIGTERM_RETURNCODE
        self._reader_polls += 1
        if self._reader_polls <= _ALIVE_POLLS:
            return None
        return _SIGTERM_RETURNCODE

    def terminate(self, grace_period_s: float = 0.5, **kwargs: object) -> None:
        del grace_period_s
        issuer = kwargs.get("issuer")
        assert isinstance(issuer, ProcessTerminationIssuer)
        self.terminate_issuers.append(issuer)

    def record_terminal_reason(self, reason: str) -> None:
        self.terminal_reasons.append(reason)


def test_a_completed_pty_attempt_is_not_reported_as_a_broken_agent(tmp_path: Path) -> None:
    master_fd, slave_fd = pty.openpty()
    handle = _ReaderLoopExitHandle(master_fd, threading.get_ident())
    supervisor = AttemptSupervisor(handle, process_teardown=_NoopProcessTeardown())
    supervisor.act(CompletionObserved())
    ctx = AgentRunCtx(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE),
        show_progress=False,
        extra_env=None,
        workspace_path=tmp_path,
        policy=TimeoutPolicy(
            idle_timeout_seconds=300.0,
            idle_poll_interval_seconds=_IDLE_POLL_INTERVAL_SECONDS,
        ),
    )
    reader = PtyLineReader(
        handle,
        "claude",
        ctx,
        FakeClock(start=0.0),
        extras=None,
        supervisor=supervisor,
    )
    try:
        assert list(reader.read_lines()) == []
        assert len(handle.terminate_issuers) == 1
    finally:
        os.close(slave_fd)
        os.close(master_fd)

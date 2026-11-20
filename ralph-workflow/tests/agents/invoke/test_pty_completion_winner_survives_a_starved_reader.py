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
from ralph.recovery.attempt_facts import TerminationIssuer

if TYPE_CHECKING:
    from pathlib import Path

_SIGTERM_RETURNCODE = -15
_MAIN_POLLS_BEFORE_THE_READER_RUNS = 50
_READER_RELEASE_TIMEOUT_SECONDS = 1.0
_COMPLETION_LINE = "Task complete.\n"


class _NoopProcessTeardown:
    def teardown_subtree(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        owner_label: str = "invoke",
    ) -> None:
        del host_pid, issuer, owner_label


class _ReaderStarvedPtyHandle:
    pid: None = None

    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd
        self._main_thread = threading.main_thread()
        self._main_polls = 0
        self._readers_may_see_the_exit = threading.Event()
        self.terminate_issuers: list[ProcessTerminationIssuer] = []

    def poll(self) -> int | None:
        if threading.current_thread() is self._main_thread:
            self._main_polls += 1
            if self._main_polls >= _MAIN_POLLS_BEFORE_THE_READER_RUNS:
                self._readers_may_see_the_exit.set()
            return _SIGTERM_RETURNCODE
        self._readers_may_see_the_exit.wait(_READER_RELEASE_TIMEOUT_SECONDS)
        return _SIGTERM_RETURNCODE

    def terminate(self, grace_period_s: float = 0.5, **kwargs: object) -> None:
        del grace_period_s
        issuer = kwargs.get("issuer")
        assert isinstance(issuer, ProcessTerminationIssuer)
        self.terminate_issuers.append(issuer)

    def record_terminal_reason(self, reason: str) -> None:
        del reason


def test_a_completion_winner_is_not_overridden_while_the_reader_is_still_draining(
    tmp_path: Path,
) -> None:
    master_fd, slave_fd = pty.openpty()
    handle = _ReaderStarvedPtyHandle(master_fd)
    supervisor = AttemptSupervisor(handle, process_teardown=_NoopProcessTeardown())
    reader = PtyLineReader(
        handle,
        "claude",
        AgentRunCtx(
            config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE),
            show_progress=False,
            extra_env=None,
            workspace_path=tmp_path,
            policy=TimeoutPolicy(idle_timeout_seconds=300.0, process_monitor_enabled=False),
        ),
        FakeClock(start=0.0),
        extras=None,
        supervisor=supervisor,
    )
    try:
        assert supervisor.act(CompletionObserved()) is not None
        facts_before = supervisor.recorder.termination()
        assert facts_before.issuer is TerminationIssuer.AGENT_EXIT
        terminate_before = list(handle.terminate_issuers)
        os.write(slave_fd, _COMPLETION_LINE.encode("utf-8"))

        assert list(reader.read_lines()) == [_COMPLETION_LINE]

        assert supervisor.recorder.termination() == facts_before
        assert handle.terminate_issuers == terminate_before
    finally:
        os.close(slave_fd)
        os.close(master_fd)

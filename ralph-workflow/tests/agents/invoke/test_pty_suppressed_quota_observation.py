from __future__ import annotations

import os
import pty
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentRunCtx,
    AttemptEnded,
    PtyLineReader,
    QuotaExhaustedError,
)
from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    CompletionObserved,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.manager import ProcessTerminationIssuer
from ralph.recovery.attempt_facts import TerminationIssuer

if TYPE_CHECKING:
    from pathlib import Path

_QUOTA_LINE = "RESOURCE_EXHAUSTED (code 429)\n"
_NANOCODER_STARTUP_LINE = "Provider 'acme' not found in agents.config.json\n"
_SIGTERM_RETURNCODE = -15
_POLL_FLIP = 6

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "private quota-path assertions replaced by public read_lines observables": (
        "the suppressed-observation contract is public reader behavior; the "
        "rewrite feeds the PTY descriptor through read_lines() and asserts "
        "delivered lines, the raised exception identity, recorder facts, and "
        "terminate issuers instead of private quota methods and _quota_error"
    ),
}


class _NoopProcessTeardown:
    def teardown_subtree(
        self,
        host_pid: int,
        *,
        issuer: ProcessTerminationIssuer,
        owner_label: str = "invoke",
    ) -> None:
        del host_pid, issuer, owner_label


class _DescriptorFedPtyHandle:
    pid: None = None

    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd
        self._polls = 0
        self.terminate_issuers: list[ProcessTerminationIssuer] = []
        self.terminal_reasons: list[str] = []

    def poll(self) -> int | None:
        self._polls += 1
        return _SIGTERM_RETURNCODE if self._polls >= _POLL_FLIP else None

    def terminate(self, grace_period_s: float = 0.5, **kwargs: object) -> None:
        del grace_period_s
        issuer = kwargs.get("issuer")
        assert isinstance(issuer, ProcessTerminationIssuer)
        self.terminate_issuers.append(issuer)

    def record_terminal_reason(self, reason: str) -> None:
        self.terminal_reasons.append(reason)


def _make_reader(
    tmp_path: Path,
    handle: _DescriptorFedPtyHandle,
    supervisor: AttemptSupervisor,
    *,
    cmd: str = "claude",
    transport: AgentTransport = AgentTransport.CLAUDE,
) -> PtyLineReader:
    ctx = AgentRunCtx(
        config=AgentConfig(cmd=cmd, transport=transport),
        show_progress=False,
        extra_env=None,
        workspace_path=tmp_path,
        policy=TimeoutPolicy(idle_timeout_seconds=300.0, process_monitor_enabled=False),
    )
    return PtyLineReader(handle, cmd, ctx, FakeClock(start=0.0), extras=None, supervisor=supervisor)


def test_completion_winner_suppresses_later_quota_observation(tmp_path: Path) -> None:
    master_fd, slave_fd = pty.openpty()
    handle = _DescriptorFedPtyHandle(master_fd)
    supervisor = AttemptSupervisor(handle, process_teardown=_NoopProcessTeardown())
    reader = _make_reader(tmp_path, handle, supervisor)
    try:
        outcome = supervisor.act(CompletionObserved())
        assert outcome is not None
        facts_before = supervisor.recorder.termination()
        assert facts_before.issuer is TerminationIssuer.AGENT_EXIT
        terminate_before = list(handle.terminate_issuers)

        os.write(slave_fd, _QUOTA_LINE.encode("utf-8"))

        assert list(reader.read_lines()) == [_QUOTA_LINE]

        assert supervisor.recorder.termination() == facts_before
        assert handle.terminate_issuers == terminate_before
    finally:
        os.close(slave_fd)
        os.close(master_fd)


def test_completion_winner_suppresses_terminal_startup_error(tmp_path: Path) -> None:
    master_fd, slave_fd = pty.openpty()
    handle = _DescriptorFedPtyHandle(master_fd)
    supervisor = AttemptSupervisor(handle, process_teardown=_NoopProcessTeardown())
    reader = _make_reader(
        tmp_path,
        handle,
        supervisor,
        cmd="nanocoder",
        transport=AgentTransport.NANOCODER,
    )
    try:
        outcome = supervisor.act(CompletionObserved())
        assert outcome is not None
        facts_before = supervisor.recorder.termination()
        terminate_before = list(handle.terminate_issuers)

        os.write(slave_fd, _NANOCODER_STARTUP_LINE.encode("utf-8"))

        assert list(reader.read_lines()) == [_NANOCODER_STARTUP_LINE]

        assert supervisor.recorder.termination() == facts_before
        assert handle.terminate_issuers == terminate_before
    finally:
        os.close(slave_fd)
        os.close(master_fd)


def test_seeded_quota_error_is_reraised_unchanged(tmp_path: Path) -> None:
    master_fd, slave_fd = pty.openpty()
    handle = _DescriptorFedPtyHandle(master_fd)
    supervisor = AttemptSupervisor(handle)
    reader = _make_reader(tmp_path, handle, supervisor)
    try:
        os.write(slave_fd, _QUOTA_LINE.encode("utf-8"))

        with pytest.raises(QuotaExhaustedError) as first:
            list(reader.read_lines())
        error = first.value
        facts_before = supervisor.recorder.termination()
        assert facts_before.issuer is TerminationIssuer.QUOTA_DETECTOR
        terminate_before = list(handle.terminate_issuers)
        assert terminate_before == [ProcessTerminationIssuer.QUOTA_DETECTOR]
        assert isinstance(error, AttemptEnded)
        facts = error.facts
        assert facts.termination is not None
        assert facts.termination.issuer is TerminationIssuer.QUOTA_DETECTOR

        with pytest.raises(QuotaExhaustedError) as second:
            list(reader.read_lines())

        assert second.value is error
        assert supervisor.recorder.termination() == facts_before
        assert handle.terminate_issuers == terminate_before
    finally:
        os.close(slave_fd)
        os.close(master_fd)

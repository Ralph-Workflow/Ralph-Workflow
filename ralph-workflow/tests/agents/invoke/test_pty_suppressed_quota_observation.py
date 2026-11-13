from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentRunCtx,
    PtyLineReader,
    QuotaExhaustedError,
    attempt_facts_from_exception,
)
from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    OperatorInterruptObserved,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.manager import ProcessTerminationIssuer
from ralph.recovery.attempt_facts import TerminationIssuer

if TYPE_CHECKING:
    from pathlib import Path

_QUOTA_LINE = "RESOURCE_EXHAUSTED (code 429)\n"
_SIGTERM_RETURNCODE = -15


class _QuotaHandle:
    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd
        self.pid: int | None = None
        self.terminate_issuers: list[ProcessTerminationIssuer] = []
        self.terminal_reasons: list[str] = []
        self._terminated = False

    def poll(self) -> int | None:
        return _SIGTERM_RETURNCODE if self._terminated else None

    def terminate(
        self,
        grace_period_s: float = 0.5,
        *,
        issuer: ProcessTerminationIssuer,
        reason: str = "",
    ) -> None:
        del grace_period_s, reason
        self.terminate_issuers.append(issuer)
        self._terminated = True

    def record_terminal_reason(self, reason: str) -> None:
        self.terminal_reasons.append(reason)


def _make_reader(
    tmp_path: Path, handle: _QuotaHandle, supervisor: AttemptSupervisor
) -> PtyLineReader:
    ctx = AgentRunCtx(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE),
        show_progress=False,
        extra_env=None,
        workspace_path=tmp_path,
        policy=TimeoutPolicy(idle_timeout_seconds=300.0, process_monitor_enabled=False),
    )
    return PtyLineReader(
        handle, "claude", ctx, FakeClock(start=0.0), extras=None, supervisor=supervisor
    )


def test_suppressed_quota_observation_keeps_original_facts(
    tmp_path: Path,
) -> None:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    handle = _QuotaHandle(master_fd)
    supervisor = AttemptSupervisor(handle)
    reader = _make_reader(tmp_path, handle, supervisor)
    teardown_issuers: list[ProcessTerminationIssuer] = []

    def _record_teardown(pid: int, *, issuer: ProcessTerminationIssuer) -> None:
        del pid
        teardown_issuers.append(issuer)

    try:
        outcome = supervisor.act(OperatorInterruptObserved(), teardown=_record_teardown)
        assert outcome is not None
        facts_before = supervisor.recorder.termination()

        reader._terminate_for_quota(_QUOTA_LINE)

        assert reader._quota_error is None
        assert supervisor.recorder.termination() == facts_before
        reader._raise_if_quota_exhausted()
        assert handle.terminate_issuers == [ProcessTerminationIssuer.OPERATOR]
        assert teardown_issuers == []
    finally:
        for fd in (reader._input_writer_fd, reader._read_fd, master_fd):
            with contextlib.suppress(OSError):
                os.close(fd)


def test_seeded_first_quota_error_is_reraised_as_same_object(
    tmp_path: Path,
) -> None:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    handle = _QuotaHandle(master_fd)
    supervisor = AttemptSupervisor(handle)
    reader = _make_reader(tmp_path, handle, supervisor)
    try:
        reader._terminate_for_quota(_QUOTA_LINE)
        first = reader._quota_error
        assert first is not None
        facts_before = supervisor.recorder.termination()
        assert [entry.issuer for entry in facts_before.log] == [TerminationIssuer.QUOTA_DETECTOR]

        reader._terminate_for_quota(_QUOTA_LINE)

        assert reader._quota_error is first
        assert supervisor.recorder.termination() == facts_before
        assert handle.terminate_issuers == [ProcessTerminationIssuer.QUOTA_DETECTOR]
        with pytest.raises(QuotaExhaustedError) as caught:
            reader._raise_if_quota_exhausted()
        assert caught.value is first
        facts = attempt_facts_from_exception(first)
        assert facts is not None
        assert facts.termination is not None
        assert facts.termination.issuer is TerminationIssuer.QUOTA_DETECTOR
    finally:
        for fd in (reader._input_writer_fd, reader._read_fd, master_fd):
            with contextlib.suppress(OSError):
                os.close(fd)

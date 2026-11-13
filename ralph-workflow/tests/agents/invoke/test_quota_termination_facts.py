from __future__ import annotations

import contextlib
import os
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    AgentRunCtx,
    ProcessLineReader,
    ProcessReaderCtx,
    PtyLineReader,
    QuotaExhaustedError,
    attempt_facts_from_exception,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.recovery.attempt_facts import TerminationIssuer, has_return_code_discrepancy

if TYPE_CHECKING:
    from pathlib import Path

_QUOTA_LINE = "RESOURCE_EXHAUSTED (code 429)\n"
_SIGTERM_RETURNCODE = -15


class _FakeSigtermOnTerminateProcess:
    stdin = None
    pid: int | None = None
    master_fd: int | None = None

    def __init__(self, stdout: object = None) -> None:
        self.stdout = stdout
        self.stderr = None
        self._terminated = False
        self.terminal_reasons: list[str] = []

    @property
    def returncode(self) -> int | None:
        return self.poll()

    def poll(self) -> int | None:
        return _SIGTERM_RETURNCODE if self._terminated else None

    def terminate(self, *, grace_period_s: float = 0.5, **_kwargs: object) -> None:
        self._terminated = True

    def record_terminal_reason(self, reason: str) -> None:
        self.terminal_reasons.append(reason)

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (0, None)


class _OneLineThenBlock:
    def __init__(self, line: str) -> None:
        self._line = line
        self._done = False

    def __iter__(self) -> _OneLineThenBlock:
        return self

    def __next__(self) -> str:
        if self._done:
            raise ValueError("closed")
        self._done = True
        return self._line


def _quota_error_from_pipe(
    tmp_path: Path,
) -> tuple[QuotaExhaustedError, _FakeSigtermOnTerminateProcess]:
    handle = _FakeSigtermOnTerminateProcess(_OneLineThenBlock(_QUOTA_LINE))
    reader = ProcessLineReader(
        handle,
        ProcessReaderCtx(
            config=AgentConfig(cmd="codex", transport=AgentTransport.CODEX),
            policy=TimeoutPolicy(idle_timeout_seconds=300.0, process_monitor_enabled=False),
            workspace_path=tmp_path,
        ),
        FakeClock(start=0.0),
    )
    with pytest.raises(QuotaExhaustedError) as caught:
        list(reader.read_lines())
    return caught.value, handle


def test_pipe_quota_kill_reports_observed_returncode_and_issuer(tmp_path: Path) -> None:
    error, handle = _quota_error_from_pipe(tmp_path)

    assert error.returncode == -15
    assert error.issuer == TerminationIssuer.QUOTA_DETECTOR.value
    assert handle.terminal_reasons == ["quota_exhausted"]
    facts = attempt_facts_from_exception(error)
    assert facts is not None
    assert facts.termination is not None
    assert facts.termination.issuer is TerminationIssuer.QUOTA_DETECTOR
    assert facts.termination.observed_returncode == -15
    assert facts.termination.reported_returncode == -15
    assert has_return_code_discrepancy(facts.termination) is False


def test_pty_quota_kill_attributes_the_detector_and_attaches_facts(tmp_path: Path) -> None:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    handle = _FakeSigtermOnTerminateProcess()
    handle.master_fd = master_fd
    ctx = AgentRunCtx(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE),
        show_progress=False,
        extra_env=None,
        workspace_path=tmp_path,
        policy=TimeoutPolicy(idle_timeout_seconds=300.0, process_monitor_enabled=False),
    )
    reader = PtyLineReader(handle, "claude", ctx, FakeClock(start=0.0), extras=None)
    try:
        reader._terminate_for_quota(_QUOTA_LINE)
        with pytest.raises(QuotaExhaustedError) as caught:
            reader._raise_if_quota_exhausted()
    finally:
        for fd in (reader._input_writer_fd, reader._read_fd, master_fd):
            with contextlib.suppress(OSError):
                os.close(fd)

    error = caught.value
    assert error.returncode == -15
    assert error.issuer == TerminationIssuer.QUOTA_DETECTOR.value
    facts = attempt_facts_from_exception(error)
    assert facts is not None
    assert facts.termination is not None
    assert facts.termination.issuer is TerminationIssuer.QUOTA_DETECTOR
    assert facts.termination.observed_returncode == -15
    assert has_return_code_discrepancy(facts.termination) is False

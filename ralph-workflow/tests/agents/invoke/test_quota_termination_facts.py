from __future__ import annotations

import contextlib
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import attempt_facts_from_exception
from ralph.agents.invoke._process_reader import ProcessLineReader
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._quota_exhausted_error import QuotaExhaustedError
from ralph.agents.invoke._types import ProcessReaderCtx
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.recovery.attempt_facts import TerminationIssuer, has_return_code_discrepancy

if TYPE_CHECKING:
    from pathlib import Path

_QUOTA_LINE = "RESOURCE_EXHAUSTED (code 429)\n"


class _KilledOnTerminate:
    """Fake process: running until terminated, then reports SIGTERM (-15)."""

    stdin = None
    pid: int | None = None

    def __init__(self, stdout: object = None) -> None:
        self.stdout = stdout
        self.stderr = None
        self._terminated = False
        self.terminal_reasons: list[str] = []

    def poll(self) -> int | None:
        return -15 if self._terminated else None

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
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


def _quota_error_from_pipe(tmp_path: Path) -> tuple[QuotaExhaustedError, _KilledOnTerminate]:
    handle = _KilledOnTerminate(_OneLineThenBlock(_QUOTA_LINE))
    reader = ProcessLineReader(
        handle,  # type: ignore[arg-type]
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
    handle = _KilledOnTerminate()
    handle.master_fd = master_fd  # type: ignore[attr-defined]
    ctx = SimpleNamespace(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE),
        policy=TimeoutPolicy(idle_timeout_seconds=300.0, process_monitor_enabled=False),
        monitor=None,
        execution_strategy=None,
        liveness_probe=None,
        waiting_listener=None,
        workspace_path=tmp_path,
    )
    reader = PtyLineReader(handle, "claude", ctx, FakeClock(start=0.0), extras=None)  # type: ignore[arg-type]
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

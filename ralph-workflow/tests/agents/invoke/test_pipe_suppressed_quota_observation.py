from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import TimeoutPolicy
from ralph.agents.invoke import (
    ProcessLineReader,
    ProcessReaderCtx,
    QuotaExhaustedError,
)
from ralph.agents.invoke.attempt_supervision import (
    AttemptSupervisor,
    CompletionObserved,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.recovery.attempt_facts import TerminationIssuer

if TYPE_CHECKING:
    from collections.abc import Iterator
    from pathlib import Path

_QUOTA_LINE = "RESOURCE_EXHAUSTED (code 429)\n"
_PLAIN_LINE = "ordinary output\n"
_SIGTERM_RETURNCODE = -15


def _line_stream(lines: list[str]) -> Iterator[str]:
    yield from lines
    raise ValueError("closed")


def _noop_teardown(pid: int, *, issuer: object) -> None:
    del pid, issuer


class _FreshStreamHandle:
    stdin = None
    stderr = None
    pid: int | None = None

    def __init__(self, streams: list[list[str]]) -> None:
        self._streams = streams
        self._accesses = 0
        self.terminate_calls = 0
        self.terminal_reasons: list[str] = []

    @property
    def stdout(self) -> Iterator[str]:
        index = min(self._accesses, len(self._streams) - 1)
        self._accesses += 1
        return _line_stream(self._streams[index])

    @property
    def returncode(self) -> int | None:
        return self.poll()

    def poll(self) -> int | None:
        return _SIGTERM_RETURNCODE if self.terminate_calls else None

    def terminate(self, grace_period_s: float = 0.5, **kwargs: object) -> None:
        del grace_period_s, kwargs
        self.terminate_calls += 1

    def record_terminal_reason(self, reason: str) -> None:
        self.terminal_reasons.append(reason)

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (0, None)


def _reader(
    handle: _FreshStreamHandle,
    supervisor: AttemptSupervisor,
    tmp_path: Path,
) -> ProcessLineReader:
    return ProcessLineReader(
        handle,
        ProcessReaderCtx(
            config=AgentConfig(cmd="codex", transport=AgentTransport.CODEX),
            policy=TimeoutPolicy(idle_timeout_seconds=300.0, process_monitor_enabled=False),
            workspace_path=tmp_path,
        ),
        FakeClock(start=0.0),
        supervisor=supervisor,
    )


def test_completion_winner_suppresses_later_quota_observation(tmp_path: Path) -> None:
    handle = _FreshStreamHandle([[_PLAIN_LINE], [_QUOTA_LINE]])
    supervisor = AttemptSupervisor(handle)
    reader = _reader(handle, supervisor, tmp_path)
    list(reader.read_lines())

    supervisor.act(CompletionObserved(), teardown=_noop_teardown)
    facts_before = supervisor.recorder.termination()
    assert facts_before.issuer is TerminationIssuer.AGENT_EXIT

    assert list(reader.read_lines()) == [_QUOTA_LINE]

    facts_after = supervisor.recorder.termination()
    assert facts_after == facts_before
    assert handle.terminate_calls == 1


def test_seeded_quota_error_is_reraised_unchanged(tmp_path: Path) -> None:
    handle = _FreshStreamHandle([[_QUOTA_LINE], [_QUOTA_LINE]])
    supervisor = AttemptSupervisor(handle)
    reader = _reader(handle, supervisor, tmp_path)

    with pytest.raises(QuotaExhaustedError) as first:
        list(reader.read_lines())
    error = first.value
    facts_before = supervisor.recorder.termination()
    assert facts_before.issuer is TerminationIssuer.QUOTA_DETECTOR

    with pytest.raises(QuotaExhaustedError) as second:
        list(reader.read_lines())

    assert second.value is error
    assert supervisor.recorder.termination() == facts_before
    assert handle.terminate_calls == 1

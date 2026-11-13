from __future__ import annotations

import contextlib
import os
from types import SimpleNamespace
from typing import TYPE_CHECKING

import pytest

from ralph.agents.idle_watchdog import PostExitVerdict, TimeoutPolicy, WatchdogFireReason
from ralph.agents.invoke import (
    AgentInactivityTimeoutError,
    AgentInvocationError,
    AgentRunCtx,
    AttemptEnded,
    IdleStreamTimeoutError,
    ProcessLineReader,
    ProcessReaderCtx,
    PtyLineReader,
    QuotaExhaustedError,
)
from ralph.agents.invoke._pty_runner import run_pty_and_read_lines
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.recovery.attempt_facts import TerminationIssuer

if TYPE_CHECKING:
    from collections.abc import Iterator
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

    @property
    def returncode(self) -> int | None:
        return self.poll()

    def poll(self) -> int | None:
        return _SIGTERM_RETURNCODE if self._terminated else None

    def terminate(self, *, grace_period_s: float = 0.5, **_kwargs: object) -> None:
        del grace_period_s
        self._terminated = True

    def record_terminal_reason(self, reason: str) -> None:
        del reason


class _BlockingPtyHandle:
    pid = 4242

    def __enter__(self) -> _BlockingPtyHandle:
        return self

    def __exit__(self, *_args: object) -> None:
        return None

    def poll(self) -> None:
        return None

    def terminate(self, *_args: object, **_kwargs: object) -> None:
        return None


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


def test_pipe_quota_termination_raises_attempt_ended_with_recorder_facts(
    tmp_path: Path,
) -> None:
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
    try:
        with pytest.raises(QuotaExhaustedError) as caught:
            list(reader.read_lines())
    finally:
        reader._unsubscribe()

    error = caught.value
    assert isinstance(error, AttemptEnded)
    assert isinstance(error, AgentInvocationError)
    assert error.facts.termination is not None
    assert error.facts.termination.issuer is TerminationIssuer.QUOTA_DETECTOR
    assert error.facts.termination.observed_returncode == _SIGTERM_RETURNCODE


def test_pipe_timeout_termination_raises_attempt_ended_with_recorder_facts(
    tmp_path: Path,
) -> None:
    handle = _FakeSigtermOnTerminateProcess(iter(()))
    reader = ProcessLineReader(
        handle,
        ProcessReaderCtx(
            config=AgentConfig(cmd="codex", transport=AgentTransport.CODEX),
            policy=TimeoutPolicy(idle_timeout_seconds=300.0, process_monitor_enabled=False),
            workspace_path=tmp_path,
        ),
        FakeClock(start=0.0),
    )
    try:
        reader.termination_recorder.observe_event("tool_use", "read")
        timeout = IdleStreamTimeoutError(5.0, WatchdogFireReason.NO_OUTPUT_AT_START)
        with pytest.raises(IdleStreamTimeoutError) as caught:
            list(reader._raise_timeout_result(([], timeout)))
    finally:
        reader._unsubscribe()

    error = caught.value
    assert isinstance(error, AttemptEnded)
    assert isinstance(error, RuntimeError)
    assert error.facts.event_kinds == ("tool_use",)


def test_pty_runner_timeout_termination_raises_attempt_ended_with_recorder_facts(
    monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    class _TimeoutPtyLineReader:
        completion_exit_sent = False

        def __init__(self, *_args: object, **_kwargs: object) -> None:
            return None

        def read_lines(self) -> Iterator[str]:
            yield "stuck\n"
            raise IdleStreamTimeoutError(0.1, WatchdogFireReason.PROCESS_EXIT_HANG)

        def discover_captured_session_id(self) -> None:
            return None

    handle = _BlockingPtyHandle()
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.get_process_manager",
        lambda: SimpleNamespace(
            spawn_pty=lambda *_a, **_k: handle,
            register_listener=lambda *_a, **_k: lambda: None,
        ),
    )
    monkeypatch.setattr("ralph.agents.invoke._pty_runner.PtyLineReader", _TimeoutPtyLineReader)
    monkeypatch.setattr(
        "ralph.agents.invoke._pty_runner.PostExitWatchdog",
        lambda *_args, **_kwargs: SimpleNamespace(
            wait_for_process_exit=lambda *_a, **_k: PostExitVerdict.QUIESCED_NO_SIGNALS
        ),
    )
    ctx = AgentRunCtx(
        config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
        show_progress=False,
        extra_env=None,
        workspace_path=tmp_path,
        policy=TimeoutPolicy(idle_timeout_seconds=300.0, process_monitor_enabled=False),
    )

    with pytest.raises(AgentInactivityTimeoutError) as caught:
        list(run_pty_and_read_lines(["claude", "--print"], ctx))

    error = caught.value
    assert isinstance(error, AttemptEnded)
    assert isinstance(error, AgentInvocationError)
    assert error.facts.termination is not None
    assert error.facts.termination.issuer is TerminationIssuer.POST_EXIT_WATCHDOG


def test_pty_line_reader_quota_termination_raises_attempt_ended_with_recorder_facts(
    tmp_path: Path,
) -> None:
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
    assert isinstance(error, AttemptEnded)
    assert isinstance(error, AgentInvocationError)
    assert error.facts.termination is not None
    assert error.facts.termination.issuer is TerminationIssuer.QUOTA_DETECTOR
    assert error.facts.termination.observed_returncode == _SIGTERM_RETURNCODE

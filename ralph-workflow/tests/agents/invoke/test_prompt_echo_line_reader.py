"""Reader-level regression coverage for prompt echoes."""

from __future__ import annotations

import os

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.invoke import (
    AgentRunCtx,
    ProcessLineReader,
    ProcessReaderCtx,
    PtyExtras,
    PtyLineReader,
)
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.process.manager import ManagedProcess, ManagedPtyProcess

_PROMPT = "plan the implementation step by step"


class _ProcessHandle(ManagedProcess):
    def __init__(self) -> None:
        pass

    @property
    def pid(self) -> int:
        return 0

    @property
    def stdout(self) -> None:
        return None

    def poll(self) -> int | None:
        return None

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s


class _PtyHandle(ManagedPtyProcess):
    @property
    def pid(self) -> int:
        return 0

    def __init__(self, master_fd: int) -> None:
        self._master_fd = master_fd

    @property
    def master_fd(self) -> int:
        return self._master_fd

    def poll(self) -> int | None:
        return None

    def terminate(self, grace_period_s: float | None = None) -> None:
        del grace_period_s

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (0, None)


def _policy() -> TimeoutPolicy:
    return TimeoutPolicy(idle_timeout_seconds=300.0)


def _process_reader() -> tuple[ProcessLineReader, IdleWatchdog]:
    clock = FakeClock(start=0.0)
    reader = ProcessLineReader(
        _ProcessHandle(),
        ProcessReaderCtx(
            config=AgentConfig(cmd="test-agent", transport=AgentTransport.GENERIC),
            policy=_policy(),
            input_prompt=_PROMPT,
        ),
        clock,
    )
    watchdog = IdleWatchdog(_policy(), clock)
    watchdog.record_invocation_start()
    return reader, watchdog


def _pty_reader() -> tuple[PtyLineReader, IdleWatchdog, int]:
    clock = FakeClock(start=0.0)
    master_fd = os.open("/dev/null", os.O_RDONLY)
    reader = PtyLineReader(
        _PtyHandle(master_fd),
        "test-agent",
        AgentRunCtx(
            config=AgentConfig(cmd="test-agent", transport=AgentTransport.CLAUDE_INTERACTIVE),
            show_progress=False,
            extra_env=None,
            workspace_path=None,
            policy=_policy(),
        ),
        clock,
        PtyExtras(input_prompt=_PROMPT),
    )
    watchdog = IdleWatchdog(_policy(), clock)
    watchdog.record_invocation_start()
    return reader, watchdog, master_fd


def test_line_readers_regression_prompt_echo_does_not_count_as_meaningful_output() -> None:
    """S-7: both transports exclude deterministic prompt echoes from LLM activity."""
    process_reader, process_watchdog = _process_reader()
    process_reader._record_line_activity(process_watchdog, _PROMPT)
    assert process_watchdog.has_meaningful_output() is False

    pty_reader, pty_watchdog, master_fd = _pty_reader()
    try:
        assert list(pty_reader._handle_queued_line(_PROMPT, pty_watchdog)) == [_PROMPT]
        assert pty_watchdog.has_meaningful_output() is False
    finally:
        os.close(master_fd)


def test_process_line_reader_regression_real_output_counts_as_meaningful_after_echoes() -> None:
    """S-7: echo traffic must not suppress a subsequent genuine LLM response."""
    reader, watchdog = _process_reader()
    for _ in range(5):
        reader._record_line_activity(watchdog, _PROMPT)
    assert watchdog.has_meaningful_output() is False

    reader._record_line_activity(watchdog, "thinking: planning next step")
    assert watchdog.has_meaningful_output() is True

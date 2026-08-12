"""Reader-level regression coverage for prompt echoes."""

from __future__ import annotations

import os
from types import SimpleNamespace
from typing import TYPE_CHECKING, cast

from ralph.agents.idle_watchdog import IdleWatchdog, TimeoutPolicy
from ralph.agents.invoke._process_reader import _ProcessLineReader
from ralph.agents.invoke._pty_extras import _PtyExtras
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._types import _ProcessReaderCtx
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig

if TYPE_CHECKING:
    from ralph.agents.invoke._agent_run_ctx import _AgentRunCtx
    from ralph.process.manager import ManagedProcess, ManagedPtyProcess


_PROMPT = "plan the implementation step by step"


class _ProcessHandle:
    pid = None
    stdout = None

    def poll(self) -> int | None:
        return None

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        del grace_period_s


class _PtyHandle:
    pid = None

    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd

    def poll(self) -> int | None:
        return None

    def terminate(self, *, grace_period_s: float = 0.5) -> None:
        del grace_period_s

    def descendant_snapshot(self) -> tuple[int, float | None]:
        return (0, None)


def _policy() -> TimeoutPolicy:
    return TimeoutPolicy(idle_timeout_seconds=300.0)


def _process_reader() -> tuple[_ProcessLineReader, IdleWatchdog]:
    clock = FakeClock(start=0.0)
    reader = _ProcessLineReader(
        cast("ManagedProcess", _ProcessHandle()),
        _ProcessReaderCtx(
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
        cast("ManagedPtyProcess", _PtyHandle(master_fd)),
        "test-agent",
        cast(
            "_AgentRunCtx",
            SimpleNamespace(
                config=AgentConfig(cmd="test-agent", transport=AgentTransport.CLAUDE_INTERACTIVE),
                policy=_policy(),
                monitor=None,
                execution_strategy=None,
                liveness_probe=None,
                waiting_listener=None,
            ),
        ),
        clock,
        _PtyExtras(input_prompt=_PROMPT),
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

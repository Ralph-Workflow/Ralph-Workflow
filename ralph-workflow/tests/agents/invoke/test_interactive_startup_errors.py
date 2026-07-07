"""Interactive agent startup/configuration error handling tests."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from ralph.agents.idle_watchdog import WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig


class _RecordingWatchdog:
    def evaluate(self, *, classify_quiet: object) -> object:
        del classify_quiet
        return WatchdogVerdict.CONTINUE


class _PlainTextStrategy:
    def classify_activity_line(self, line: str) -> None:
        del line

    def observe_line(self, line: str) -> None:
        del line


def _build_nanocoder_reader() -> PtyLineReader:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    handle = SimpleNamespace(
        master_fd=master_fd,
        pid=None,
        terminate=lambda grace_period_s=None: None,
        poll=lambda: None,
    )
    ctx = SimpleNamespace(
        config=AgentConfig(cmd="nanocoder", transport=AgentTransport.NANOCODER),
        policy=TimeoutPolicy(idle_timeout_seconds=300.0),
        monitor=None,
        execution_strategy=_PlainTextStrategy(),
        liveness_probe=None,
        waiting_listener=None,
    )
    try:
        return PtyLineReader(handle, "nanocoder", ctx, FakeClock(start=0.0), extras=None)
    finally:
        os.close(master_fd)


def test_nanocoder_provider_error_line_raises_agent_invocation_error() -> None:
    """Nanocoder startup/config errors must fail fast instead of waiting for idle timeout."""
    reader = _build_nanocoder_reader()
    line = "Provider 'minimax' not found in agents.config.json. Available providers: \n"
    iterator = reader._handle_queued_line(line, _RecordingWatchdog())

    assert next(iterator) == line
    with pytest.raises(AgentInvocationError) as exc_info:
        next(iterator)

    assert exc_info.value.agent_name == "nanocoder"
    assert exc_info.value.returncode == 1
    assert "Provider 'minimax' not found in agents.config.json" in str(exc_info.value)
    assert exc_info.value.parsed_output == [line.strip()]

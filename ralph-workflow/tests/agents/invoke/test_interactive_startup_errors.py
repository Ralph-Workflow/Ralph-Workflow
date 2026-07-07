"""Interactive agent startup/configuration error handling tests."""

from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from ralph.agents.idle_watchdog import WatchdogFireReason, WatchdogVerdict
from ralph.agents.idle_watchdog.timeout_policy import TimeoutPolicy
from ralph.agents.invoke._errors import AgentInvocationError
from ralph.agents.invoke._pty_extras import _PtyExtras
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig


class _RecordingWatchdog:
    def evaluate(self, *, classify_quiet: object) -> object:
        del classify_quiet
        return WatchdogVerdict.CONTINUE


class _EmptyEvidenceSummary:
    def to_dict_list(self) -> list[object]:
        return []


class _FireWatchdog:
    last_fire_reason = WatchdogFireReason.NO_OUTPUT_DEADLINE
    last_alive_by = None

    def last_evidence_summary(self, _now: float) -> _EmptyEvidenceSummary:
        return _EmptyEvidenceSummary()


class _PlainTextStrategy:
    def classify_activity_line(self, line: str) -> None:
        del line

    def observe_line(self, line: str) -> None:
        del line


def _build_nanocoder_reader(extras: _PtyExtras | None = None) -> PtyLineReader:
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
        return PtyLineReader(handle, "nanocoder", ctx, FakeClock(start=0.0), extras=extras)
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


def test_nanocoder_initial_input_waits_for_prompt_before_write(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Nanocoder must not receive the task before its TUI input prompt is ready."""
    writes: list[str] = []

    def fake_write_pty_input(_fd: int, text: str, *, lock: object) -> None:
        del _fd, lock
        writes.append(text)

    monkeypatch.setattr(
        "ralph.agents.invoke._pty_line_reader._write_pty_input",
        fake_write_pty_input,
    )
    reader = _build_nanocoder_reader(
        _PtyExtras(
            initial_input="Read and follow the full task in /tmp/PROMPT.md.\r",
            initial_input_ready_markers=("What would you like me to help with?",),
        )
    )
    watchdog = _RecordingWatchdog()

    reader._send_initial_input()
    assert writes == []

    list(reader._handle_queued_line("✻ Welcome to Nanocoder 1.28.1 ✻\n", watchdog))
    assert writes == []

    list(reader._handle_queued_line("What would you like me to help with?\n", watchdog))
    assert writes == ["Read and follow the full task in /tmp/PROMPT.md.\r"]


def test_nanocoder_idle_fire_reports_pending_initial_input() -> None:
    """Idle failures before task submission must be diagnosable as integration errors."""
    reader = _build_nanocoder_reader(
        _PtyExtras(
            initial_input="Read and follow the full task in /tmp/PROMPT.md.\r",
            initial_input_ready_markers=("What would you like me to help with?",),
        )
    )

    fire_result = reader._check_fire(_FireWatchdog(), WatchdogVerdict.FIRE)

    assert fire_result is not None
    _pending_lines, exc = fire_result
    assert exc.diagnostic is not None
    assert exc.diagnostic["initial_input_pending"] is True
    assert exc.diagnostic["initial_input_ready"] is False
    assert exc.diagnostic["initial_input_ready_markers"] == [
        "What would you like me to help with?"
    ]

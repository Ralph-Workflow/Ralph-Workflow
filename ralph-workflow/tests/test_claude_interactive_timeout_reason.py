from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from ralph.agents.activity import AgentActivityKind
from ralph.agents.idle_watchdog import TimeoutPolicy, WatchdogFireReason, WatchdogVerdict
from ralph.agents.invoke import AgentInactivityTimeoutError
from ralph.agents.invoke._errors import _IdleStreamTimeoutError
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._pty_runner import run_pty_and_read_lines
from ralph.agents.timeout_clock import FakeClock


class _FakeHandle:
    def __init__(self, master_fd: int) -> None:
        self.master_fd = master_fd
        self.terminate_calls: list[float | None] = []

    def poll(self) -> int | None:
        return None

    def terminate(self, grace_period_s: float | None = None) -> None:
        self.terminate_calls.append(grace_period_s)

    def __enter__(self) -> _FakeHandle:
        return self

    def __exit__(self, exc_type: object, exc: object, tb: object) -> None:
        del exc_type, exc, tb


class _FakePtyManager:
    def __init__(self, handle: _FakeHandle) -> None:
        self._handle = handle

    def spawn_pty(self, *args: object, **kwargs: object) -> _FakeHandle:
        del args, kwargs
        return self._handle


class _RaisingPtyLineReader:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def read_lines(self) -> object:
        if False:
            yield ""
        raise _IdleStreamTimeoutError(
            300.0,
            WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
            diagnostic={"last_tool_name": "read_file"},
        )


class _SessionCapturingRaisingPtyLineReader:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def read_lines(self) -> object:
        yield '{"type":"session","session_id":"session-from-stream"}\n'
        raise _IdleStreamTimeoutError(
            300.0,
            WatchdogFireReason.STALLED_AFTER_TOOL_RESULT,
            diagnostic={"last_tool_name": "read_file"},
        )


class _FakeWatchdog:
    def __init__(self) -> None:
        self.last_fire_reason: WatchdogFireReason | None = None

    def record_activity(self) -> None:
        return None

    def record_lifecycle_activity(self) -> None:
        return None

    def record_error_activity(self, message: str) -> None:
        del message

    def record_progress_report(self, message: str) -> None:
        del message

    def record_tool_result_activity(self) -> None:
        return None

    def evaluate(self, *, classify_quiet: object) -> WatchdogVerdict:
        del classify_quiet
        return WatchdogVerdict.CONTINUE


class _FakeStrategy:
    def classify_activity_line(self, line: str) -> object:
        if line == "tool\n":
            return SimpleNamespace(kind=AgentActivityKind.TOOL_RESULT, raw="claude result: ok")
        if line == "lifecycle\n":
            return SimpleNamespace(kind=AgentActivityKind.LIFECYCLE, raw="claude/session")
        return None

    def observe_line(self, line: str) -> None:
        del line

    def classify_quiet(self, handle: object, probe: object) -> object:
        del handle, probe
        return None


def test_pty_line_reader_reclassifies_no_output_deadline_after_tool_result() -> None:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakeHandle(master_fd)
        clock = FakeClock(start=25.0)
        ctx = SimpleNamespace(
            policy=TimeoutPolicy(idle_timeout_seconds=300.0),
            monitor=None,
            execution_strategy=None,
            liveness_probe=None,
            waiting_listener=None,
        )
        reader = PtyLineReader(handle, "claude", ctx, clock, extras=None)
        reader._last_activity_kind = AgentActivityKind.TOOL_RESULT
        reader._awaiting_post_tool_result_progress = True
        reader._last_tool_use_name = "read_file"
        reader._last_tool_result_at = 10.0
        reader._last_tool_result_excerpt = (
            'claude result: {"path": "tests/test_claude_interactive_pty.py"}'
        )

        watchdog = SimpleNamespace(
            last_fire_reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
            last_evidence_summary=lambda _now: (),
        )

        result = reader._check_fire(watchdog, WatchdogVerdict.FIRE)

        assert result is not None
        _pending_lines, exc = result
        assert exc.reason == WatchdogFireReason.STALLED_AFTER_TOOL_RESULT
        assert "read_file" in str(exc)
        assert "after receiving a tool result" in str(exc)
    finally:
        os.close(master_fd)


def test_pty_line_reader_keeps_stalled_after_tool_result_reason_after_lifecycle_noise() -> None:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakeHandle(master_fd)
        clock = FakeClock(start=25.0)
        ctx = SimpleNamespace(
            policy=TimeoutPolicy(idle_timeout_seconds=300.0),
            monitor=None,
            execution_strategy=None,
            liveness_probe=None,
            waiting_listener=None,
        )
        reader = PtyLineReader(handle, "claude", ctx, clock, extras=None)
        reader._last_activity_kind = AgentActivityKind.LIFECYCLE
        reader._awaiting_post_tool_result_progress = True
        reader._last_tool_use_name = "read_file"
        reader._last_tool_result_at = 10.0

        watchdog = SimpleNamespace(
            last_fire_reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
            last_evidence_summary=lambda _now: (),
        )

        result = reader._check_fire(watchdog, WatchdogVerdict.FIRE)

        assert result is not None
        _pending_lines, exc = result
        assert exc.reason == WatchdogFireReason.STALLED_AFTER_TOOL_RESULT
    finally:
        os.close(master_fd)


def test_run_pty_and_read_lines_preserves_resumable_session_id_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakeHandle(master_fd)
        monkeypatch.setattr(
            "ralph.agents.invoke._pty_runner.get_process_manager",
            lambda: _FakePtyManager(handle),
        )
        monkeypatch.setattr(
            "ralph.agents.invoke._pty_runner.PtyLineReader",
            _RaisingPtyLineReader,
        )
        ctx = SimpleNamespace(
            clock=FakeClock(),
            workspace_path=None,
            extra_env={},
            config=SimpleNamespace(cmd="claude"),
            show_progress=False,
            policy=SimpleNamespace(process_exit_wait_seconds=0.1),
            execution_strategy=None,
            liveness_probe=None,
            waiting_listener=None,
            monitor=None,
            required_artifact=None,
            evaluate_completion_fn=lambda *args, **kwargs: None,
        )

        with pytest.raises(AgentInactivityTimeoutError) as exc_info:
            list(
                run_pty_and_read_lines(
                    ["claude"],
                    ctx,
                    extras=SimpleNamespace(
                        expected_session_id="session-keep",
                        stop_sentinel_path=None,
                        permission_prompt_listener=None,
                    ),
                )
            )

        assert exc_info.value.resumable_session_id == "session-keep"
    finally:
        os.close(master_fd)


def test_run_pty_and_read_lines_uses_streamed_session_id_on_timeout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakeHandle(master_fd)
        monkeypatch.setattr(
            "ralph.agents.invoke._pty_runner.get_process_manager",
            lambda: _FakePtyManager(handle),
        )
        monkeypatch.setattr(
            "ralph.agents.invoke._pty_runner.PtyLineReader",
            _SessionCapturingRaisingPtyLineReader,
        )
        ctx = SimpleNamespace(
            clock=FakeClock(),
            workspace_path=None,
            extra_env={},
            config=SimpleNamespace(cmd="claude"),
            show_progress=False,
            policy=SimpleNamespace(process_exit_wait_seconds=0.1),
            execution_strategy=None,
            liveness_probe=None,
            waiting_listener=None,
            monitor=None,
            required_artifact=None,
            evaluate_completion_fn=lambda *args, **kwargs: None,
        )

        with pytest.raises(AgentInactivityTimeoutError) as exc_info:
            list(
                run_pty_and_read_lines(
                    ["claude"],
                    ctx,
                    extras=SimpleNamespace(
                        expected_session_id="session-keep",
                        stop_sentinel_path=None,
                        permission_prompt_listener=None,
                    ),
                )
            )

        assert exc_info.value.resumable_session_id == "session-from-stream"
    finally:
        os.close(master_fd)


def test_handle_queued_line_keeps_post_tool_result_marker_through_lifecycle_noise() -> None:
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakeHandle(master_fd)
        clock = FakeClock(start=25.0)
        ctx = SimpleNamespace(
            policy=TimeoutPolicy(idle_timeout_seconds=300.0),
            monitor=None,
            execution_strategy=_FakeStrategy(),
            liveness_probe=None,
            waiting_listener=None,
        )
        reader = PtyLineReader(handle, "claude", ctx, clock, extras=None)
        watchdog = _FakeWatchdog()

        list(reader._handle_queued_line("tool\n", watchdog))
        list(reader._handle_queued_line("lifecycle\n", watchdog))

        assert reader._awaiting_post_tool_result_progress is True
    finally:
        os.close(master_fd)

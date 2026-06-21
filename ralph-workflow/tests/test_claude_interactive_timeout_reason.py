from __future__ import annotations

import os
from types import SimpleNamespace

import pytest

from ralph.agents.activity import AgentActivityKind
from ralph.agents.idle_watchdog import TimeoutPolicy, WatchdogFireReason, WatchdogVerdict
from ralph.agents.idle_watchdog._evidence_tier import EvidenceSummary
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke import AgentInactivityTimeoutError
from ralph.agents.invoke._errors import _IdleStreamTimeoutError
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.invoke._pty_runner import run_pty_and_read_lines
from ralph.agents.timeout_clock import FakeClock
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig
from ralph.recovery.failure_category import FailureCategory
from ralph.recovery.failure_classifier import FailureClassifier


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
            config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
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
            last_evidence_summary=lambda _now: EvidenceSummary(),
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
            config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
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
            last_evidence_summary=lambda _now: EvidenceSummary(),
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
            config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
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


def test_pty_line_reader_check_fire_attaches_typed_idle_watchdog_cause() -> None:
    """AC-05: PTY watchdog path propagates the typed ``IdleWatchdogKilledError``.

    The non-PTY watchdog path (``_process_reader._check_fire``) builds
    an ``IdleWatchdogKilledError`` with the watchdog's typed
    attributes (``reason``, ``signal``) and attaches it as
    ``__cause__`` of the returned ``_IdleStreamTimeoutError``. The
    failure_classifier's typed-attribute branch in
    ``failure_classifier.py`` walks the ``__cause__`` chain to find
    the typed cause, so the watchdog kill is classified as AGENT
    (not as a connectivity blip) regardless of how deep the typed
    cause is buried.

    This test pins the PTY path to the same contract: the PTY
    ``_check_fire`` must also build an ``IdleWatchdogKilledError``
    and attach it as ``__cause__`` of the wrapper. Without this
    contract, a PTY-backed agent's watchdog kill would be
    substring-classified, relabeling a SIGTERM as a connectivity
    blip because the wrapper's message happens to contain the word
    "timeout".
    """
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakeHandle(master_fd)
        clock = FakeClock(start=25.0)
        ctx = SimpleNamespace(
            config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
            policy=TimeoutPolicy(idle_timeout_seconds=300.0),
            monitor=None,
            execution_strategy=None,
            liveness_probe=None,
            waiting_listener=None,
        )
        reader = PtyLineReader(handle, "claude", ctx, clock, extras=None)

        watchdog = SimpleNamespace(
            last_fire_reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
            last_evidence_summary=lambda _now: EvidenceSummary(),
        )

        result = reader._check_fire(watchdog, WatchdogVerdict.FIRE)
        assert result is not None
        _pending_lines, exc = result

        # The PTY watchdog path now propagates the typed watchdog
        # exception as the __cause__ of the stream-timeout wrapper.
        typed_cause = exc.__cause__
        assert typed_cause is not None, (
            "PTY watchdog path must attach IdleWatchdogKilledError as __cause__"
        )
        assert isinstance(typed_cause, IdleWatchdogKilledError), (
            f"PTY watchdog __cause__ must be IdleWatchdogKilledError, "
            f"got {type(typed_cause).__name__}"
        )
        # The typed exception carries the watchdog's authoritative
        # attributes so the failure_classifier can consult
        # exc.reason / exc.signal directly.
        assert typed_cause.reason == WatchdogFireReason.NO_OUTPUT_DEADLINE.value
        assert typed_cause.signal == 15
    finally:
        os.close(master_fd)


def test_pty_line_reader_check_fire_classifies_as_agent_via_chain() -> None:
    """AC-05 end-to-end: a PTY watchdog kill classifies as AGENT, not ENVIRONMENTAL.

    A regression test that proves the full chain
    ``_IdleStreamTimeoutError`` (PTY wrapper, with the watchdog's
    ``IdleWatchdogKilledError`` attached as ``__cause__``) is
    classified as AGENT by the failure_classifier. Without the typed
    cause attached, the classifier would fall through to the
    text-based matching, which would relabel the SIGTERM as
    ENVIRONMENTAL because the wrapper message contains the word
    "timeout".
    """
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakeHandle(master_fd)
        clock = FakeClock(start=25.0)
        ctx = SimpleNamespace(
            config=AgentConfig(cmd="claude", transport=AgentTransport.CLAUDE_INTERACTIVE),
            policy=TimeoutPolicy(idle_timeout_seconds=300.0),
            monitor=None,
            execution_strategy=None,
            liveness_probe=None,
            waiting_listener=None,
        )
        reader = PtyLineReader(handle, "claude", ctx, clock, extras=None)

        watchdog = SimpleNamespace(
            last_fire_reason=WatchdogFireReason.NO_OUTPUT_DEADLINE,
            last_evidence_summary=lambda _now: EvidenceSummary(),
        )

        result = reader._check_fire(watchdog, WatchdogVerdict.FIRE)
        assert result is not None
        _pending_lines, exc = result

        # The failure_classifier must see the typed watchdog cause
        # via the __cause__ walk and classify as AGENT, not
        # ENVIRONMENTAL.
        classified = FailureClassifier().classify(
            exc, phase="pty_watchdog", agent="claude", connectivity_state="online"
        )
        assert classified.category == FailureCategory.AGENT, (
            f"PTY watchdog kill must classify as AGENT, got {classified.category}"
        )
    finally:
        os.close(master_fd)


class _NoOutputAtStartRaisingPtyLineReader:
    def __init__(self, *args: object, **kwargs: object) -> None:
        del args, kwargs

    def read_lines(self) -> object:
        if False:
            yield ""
        raise _IdleStreamTimeoutError(
            30.0,
            WatchdogFireReason.NO_OUTPUT_AT_START,
            diagnostic={"invocation_elapsed": 30.0},
        )


def test_run_pty_and_read_lines_resume_safe_for_no_output_at_start(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """PTY runner preserves session_resume_safe=True for NO_OUTPUT_AT_START.

    The PTY runner must raise ``AgentInactivityTimeoutError`` with
    ``session_resume_safe=True`` when the watchdog fires
    ``NO_OUTPUT_AT_START`` -- the agent produced no output for the
    configured ``no_output_at_start_seconds`` (default 30s). A
    watchdog kill at the NO_OUTPUT_AT_START threshold is NOT a phase
    transition: the same agent session should be resumed via the
    ``resumable_session_id`` so the recovery controller does NOT
    restart from scratch.

    This pins the PTY-path membership of ``NO_OUTPUT_AT_START`` in the
    session_resume_safe whitelist (the closed literal in
    ``_pty_runner.py``).
    """
    master_fd = os.open("/dev/null", os.O_RDONLY)
    try:
        handle = _FakeHandle(master_fd)
        monkeypatch.setattr(
            "ralph.agents.invoke._pty_runner.get_process_manager",
            lambda: _FakePtyManager(handle),
        )
        monkeypatch.setattr(
            "ralph.agents.invoke._pty_runner.PtyLineReader",
            _NoOutputAtStartRaisingPtyLineReader,
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

        assert exc_info.value.session_resume_safe is True, (
            f"Expected session_resume_safe=True for NO_OUTPUT_AT_START, "
            f"got {exc_info.value.session_resume_safe}"
        )
        assert exc_info.value.reason == WatchdogFireReason.NO_OUTPUT_AT_START, (
            f"Expected reason=NO_OUTPUT_AT_START, got {exc_info.value.reason}"
        )
        assert exc_info.value.resumable_session_id == "session-keep", (
            f"Expected resumable_session_id='session-keep', "
            f"got {exc_info.value.resumable_session_id!r}"
        )
    finally:
        os.close(master_fd)

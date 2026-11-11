from __future__ import annotations

import threading
from collections import deque
from unittest.mock import MagicMock

from loguru import logger

from ralph.agents.execution_state import AgentExecutionState
from ralph.agents.idle_watchdog import (
    IdleWatchdog,
    TimeoutPolicy,
    WatchdogFireReason,
    WatchdogVerdict,
)
from ralph.agents.idle_watchdog_kill import IdleWatchdogKilledError
from ralph.agents.invoke._errors import _IdleStreamTimeoutError
from ralph.agents.invoke._pty_line_reader import PtyLineReader
from ralph.agents.timeout_clock import FakeClock


def _capture_loguru_warnings() -> tuple[list[str], int]:
    captured: list[str] = []

    def _sink(message: str) -> None:
        captured.append(message)

    handler_id = logger.add(
        _sink,
        level="WARNING",
        format="{message}",
        filter=lambda record: "idle_watchdog" in (record["extra"].get("component") or ""),
    )
    return captured, handler_id


def _remove_loguru_handler(handler_id: int) -> None:
    logger.remove(handler_id)


def _make_pty_line_reader_with_session_id(
    session_id: str,
) -> tuple[PtyLineReader, IdleWatchdog, FakeClock]:
    handle = MagicMock()
    handle.terminate = MagicMock()
    handle.pid = 12345
    handle.descendant_snapshot = MagicMock(return_value=(0, None))
    handle.master_fd = 7
    policy = TimeoutPolicy(
        idle_timeout_seconds=60.0,
        no_output_at_start_seconds=10.0,
        no_progress_quiet_seconds=None,
        activity_evidence_ttl_seconds=180.0,
    )
    clock = FakeClock(start=0.0)
    reader = PtyLineReader.__new__(PtyLineReader)
    reader._handle = handle
    reader._agent_name = "test-agent"
    reader._policy = policy
    reader._clock = clock
    reader._lines_queue = []
    reader._lines_lock = MagicMock()
    reader._last_hard_stop = [None]
    reader._last_activity_kind = "output_line"
    reader._awaiting_post_tool_result_progress = False
    reader._last_tool_result_at = None
    reader._last_tool_result_excerpt = None
    reader._last_tool_use_name = None
    reader._captured_session_id = session_id
    reader._transcript_session_ids = deque([session_id], maxlen=64)
    reader._transcript_session_ids_lock = threading.Lock()
    watchdog = IdleWatchdog(policy, clock)
    return reader, watchdog, clock


def test_pty_record_transcript_session_id_populates_captured_cache() -> None:
    reader, _watchdog, _clock = _make_pty_line_reader_with_session_id("initial")
    assert reader._captured_session_id == "initial"
    reader._record_transcript_session_id(
        "\x1b[32mClaude session ready. Session ID: pty-abc123\x1b[0m"
    )
    assert reader._captured_session_id == "pty-abc123", (
        f"_record_transcript_session_id MUST update _captured_session_id;"
        f" got {reader._captured_session_id!r}"
    )


def test_pty_check_fire_threads_resumable_session_id_to_typed_exception() -> None:
    captured = "pty-abc123"
    reader, watchdog, clock = _make_pty_line_reader_with_session_id(captured)
    watchdog.record_invocation_start()

    def _active() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE

    clock.advance(11.0)
    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    fire_result = reader._check_fire(watchdog, verdict)
    assert fire_result is not None, "_check_fire MUST return a fire result when verdict is FIRE"
    _pending_lines, wrapper = fire_result
    assert isinstance(wrapper, _IdleStreamTimeoutError)
    typed_exc = wrapper.__cause__
    assert isinstance(typed_exc, IdleWatchdogKilledError)
    assert typed_exc.resumable_session_id == captured, (
        f"typed exception MUST carry resumable_session_id={captured!r};"
        f" got {typed_exc.resumable_session_id!r}"
    )


def test_pty_check_fire_threads_resumable_session_id_to_merged_diag() -> None:
    captured = "pty-merged-789"
    reader, watchdog, clock = _make_pty_line_reader_with_session_id(captured)
    watchdog.record_invocation_start()
    clock.advance(11.0)

    def _active() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE

    verdict = watchdog.evaluate(classify_quiet=_active)
    fire_result = reader._check_fire(watchdog, verdict)
    assert fire_result is not None
    _pending_lines, wrapper = fire_result
    diag = wrapper.diagnostic
    assert isinstance(diag, dict), f"diagnostic MUST be a dict; got {type(diag)}"
    assert diag.get("resumable_session_id") == captured, (
        f"merged_diag MUST carry resumable_session_id={captured!r};"
        f" got {diag.get('resumable_session_id')!r}"
    )
    assert diag.get("resume_safe") is True, (
        f"merged_diag MUST carry resume_safe=True for NO_OUTPUT_AT_START;"
        f" got {diag.get('resume_safe')!r}"
    )
    assert "watchdog_snapshot" in diag, (
        f"merged_diag MUST include watchdog_snapshot; got keys={list(diag.keys())}"
    )
    snapshot = diag["watchdog_snapshot"]
    assert isinstance(snapshot, dict)
    assert snapshot.get("last_fire_reason") == WatchdogFireReason.NO_OUTPUT_AT_START.value, (
        f"watchdog_snapshot.last_fire_reason MUST be the canonical"
        f" WatchdogFireReason.value; got {snapshot.get('last_fire_reason')!r}"
    )


def test_pty_check_fire_emits_canonical_fire_warning() -> None:
    captured_warnings: list[str] = []
    handler_id = logger.add(
        lambda msg: captured_warnings.append(str(msg)),
        level="WARNING",
        format="{message}",
    )
    try:
        captured = "pty-log-999"
        reader, watchdog, clock = _make_pty_line_reader_with_session_id(captured)
        watchdog.record_invocation_start()
        clock.advance(11.0)

        def _active() -> AgentExecutionState:
            return AgentExecutionState.ACTIVE

        verdict = watchdog.evaluate(classify_quiet=_active)
        fire_result = reader._check_fire(watchdog, verdict)
        assert fire_result is not None
    finally:
        logger.remove(handler_id)
    matching = [w for w in captured_warnings if "idle watchdog: FIRE" in w]
    assert matching, (
        f"PTY _check_fire MUST trigger the canonical watchdog-kill"
        f" warning; warnings={captured_warnings[:3]}"
    )


def test_pty_check_fire_resume_safe_for_non_resumable_reason() -> None:
    captured = "pty-no-resume"
    reader, watchdog, clock = _make_pty_line_reader_with_session_id(captured)
    watchdog.record_invocation_start()
    clock.advance(11.0)
    watchdog._last_fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    verdict = WatchdogVerdict.FIRE
    fire_result = reader._check_fire(watchdog, verdict)
    assert fire_result is not None, "_check_fire MUST return a fire result for the test scenario"
    _pending_lines, wrapper = fire_result
    diag = wrapper.diagnostic
    assert isinstance(diag, dict)
    assert diag.get("resume_safe") is False, (
        f"merged_diag.resume_safe MUST be False for CHILDREN_PERSIST_TOO_LONG;"
        f" got {diag.get('resume_safe')!r}"
    )

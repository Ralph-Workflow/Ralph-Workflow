"""Pin: PTY watchdog fire path carries the same resume metadata as subprocess.

The pre-fix bug: the PTY ``_check_fire`` in ``_pty_line_reader.py``
did NOT include ``resume_safe``, ``resumable_session_id``, or
``watchdog_snapshot`` in the merged_diag or in the typed
``IdleWatchdogKilledError``. The subprocess path in
``_process_reader.py:602-703`` includes all three. The PTY path was
therefore less diagnosable AND could not survive a watchdog-kill
-> resume flow on the PTY transport (the recovery controller had
no session id to thread through ``agent_retry_intent.session_id``).

The fix: PTY ``_check_fire`` now:

1. Computes ``resume_safe`` via the canonical helper
   ``_process_reader._is_resumable_fire_reason`` (single source of
   truth for the resumable-reason set).
2. Logs the resume metadata via the canonical loguru warning so an
   operator can grep for ``resume_safe=`` / ``resumable_session_id=``
   in the post-mortem log.
3. Threads ``captured_session_id`` (from the reader-level cache
   populated by ``_record_transcript_session_id``) into the typed
   ``IdleWatchdogKilledError.resumable_session_id`` AND the
   merged_diag.
4. Adds ``resumable_session_id``, ``resume_safe``, and the
   ``watchdog_snapshot`` (via ``IdleWatchdog.diagnostic_snapshot``)
   to the merged_diag so a log-only consumer of the diagnostic
   payload sees the captured id and the watchdog snapshot.

This test exercises the PTY ``_check_fire`` end-to-end via a
hand-crafted ``PtyLineReader`` instance that:

* Has a stub ``_handle`` (``MagicMock``) with ``terminate`` /
  ``pid`` / ``descendant_snapshot`` / ``master_fd`` shims.
* Has a stub ``_policy`` (TimeoutPolicy with
  ``no_output_at_start_seconds=10s`` so the fire path triggers).
* Uses ``FakeClock`` for deterministic timing.
* Constructs the watchdog inline so the fire path can run.

The test asserts:

1. ``captured_session_id`` flows from ``_record_transcript_session_id``
   to the reader-level cache.
2. ``resume_safe`` is ``True`` for ``NO_OUTPUT_AT_START`` (the
   canonical resumable reason set includes it).
3. The merged_diag carries ``resumable_session_id``,
   ``resume_safe``, and ``watchdog_snapshot``.
4. The typed ``IdleWatchdogKilledError.__cause__`` carries the
   captured ``resumable_session_id``.

All tests use ``MagicMock`` and ``FakeClock``; no real subprocess,
no real PTY, no real sleep, no real network.
"""

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
    """Build a PtyLineReader with a captured PTY-visible session id.

    Bypasses the real ``__init__`` (which requires a real
    ``ManagedPtyProcess``) and wires the minimum state the
    ``_check_fire`` method needs: ``_handle`` (terminate / pid /
    descendant_snapshot / master_fd shims), ``_policy``, ``_clock``,
    ``_captured_session_id``, ``_transcript_session_ids`` /
    ``_transcript_session_ids_lock``, ``_lines_queue`` /
    ``_lines_lock``, ``_last_hard_stop``, ``_last_activity_kind``.
    """
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
    reader._initial_input = None
    reader._initial_input_ready = True
    reader._initial_input_ready_marker_labels = ()
    reader._initial_input_sent = False
    reader._captured_session_id = session_id
    reader._transcript_session_ids = deque([session_id], maxlen=64)
    reader._transcript_session_ids_lock = threading.Lock()
    watchdog = IdleWatchdog(policy, clock)
    return reader, watchdog, clock


def test_pty_record_transcript_session_id_populates_captured_cache() -> None:
    """``_record_transcript_session_id`` MUST populate
    ``_captured_session_id`` so the watchdog-kill -> resume path
    sees the captured id without re-walking the PTY queue.
    """
    reader, _watchdog, _clock = _make_pty_line_reader_with_session_id("initial")
    # Pre-condition: ``_captured_session_id`` was set in the helper.
    assert reader._captured_session_id == "initial"
    # Drive ``_record_transcript_session_id`` with a new TUI-banner
    # line; the cache MUST update to the new id.
    reader._record_transcript_session_id(
        "\x1b[32mClaude session ready. Session ID: pty-abc123\x1b[0m"
    )
    assert reader._captured_session_id == "pty-abc123", (
        f"_record_transcript_session_id MUST update _captured_session_id;"
        f" got {reader._captured_session_id!r}"
    )


def test_pty_check_fire_threads_resumable_session_id_to_typed_exception() -> None:
    """``_check_fire`` MUST populate
    ``IdleWatchdogKilledError.resumable_session_id`` on the typed
    exception so the failure classifier can read the captured id
    end-to-end via the ``__cause__`` chain.

    The pre-fix bug: the PTY path did NOT pass
    ``resumable_session_id`` to ``IdleWatchdogKilledError`` so the
    recovery controller had no session id to thread into the
    resume intent.
    """
    captured = "pty-abc123"
    reader, watchdog, clock = _make_pty_line_reader_with_session_id(captured)
    watchdog.record_invocation_start()
    # Drive the fire path naturally: advance past the
    # no_output_at_start threshold with no recorded activity.

    def _active() -> AgentExecutionState:
        return AgentExecutionState.ACTIVE

    clock.advance(11.0)
    verdict = watchdog.evaluate(classify_quiet=_active)
    assert verdict == WatchdogVerdict.FIRE
    fire_result = reader._check_fire(watchdog, verdict)
    assert fire_result is not None, (
        "_check_fire MUST return a fire result when verdict is FIRE"
    )
    _pending_lines, wrapper = fire_result
    assert isinstance(wrapper, _IdleStreamTimeoutError)
    typed_exc = wrapper.__cause__
    assert isinstance(typed_exc, IdleWatchdogKilledError)
    assert typed_exc.resumable_session_id == captured, (
        f"typed exception MUST carry resumable_session_id={captured!r};"
        f" got {typed_exc.resumable_session_id!r}"
    )


def test_pty_check_fire_threads_resumable_session_id_to_merged_diag() -> None:
    """``_check_fire`` MUST include ``resumable_session_id``,
    ``resume_safe``, and ``watchdog_snapshot`` in the merged_diag
    so an on-call grep of the diagnostic payload sees the captured
    id and the watchdog snapshot.
    """
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
    # ``NO_OUTPUT_AT_START`` is in the canonical resumable-reason set
    # so ``resume_safe`` MUST be True.
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
    """``_check_fire`` MUST trigger the canonical watchdog-kill
    warning via ``IdleWatchdog._emit_fire_log`` so an operator
    can grep the post-mortem log for the fire reason. The
    ``resume_safe`` / ``resumable_session_id`` surface is the
    typed ``IdleWatchdogKilledError`` attached as ``__cause__``
    and the merged_diag payload; the canonical warning is the
    same one the subprocess path emits.
    """
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
    # The canonical warning MUST contain the fire reason and the
    # threshold. The PTY ``_check_fire`` triggers
    # ``IdleWatchdog._emit_fire_log`` which produces a
    # ``idle watchdog: FIRE reason=<reason> ...`` line.
    matching = [w for w in captured_warnings if "idle watchdog: FIRE" in w]
    assert matching, (
        f"PTY _check_fire MUST trigger the canonical watchdog-kill"
        f" warning; warnings={captured_warnings[:3]}"
    )


def test_pty_check_fire_resume_safe_for_non_resumable_reason() -> None:
    """``_check_fire`` MUST set ``resume_safe=False`` for a
    NON-resumable fire reason so the recovery controller does not
    emit a resume intent for a fire that cannot be safely resumed
    (e.g. ``CHILDREN_PERSIST_TOO_LONG``).
    """
    captured = "pty-no-resume"
    reader, watchdog, clock = _make_pty_line_reader_with_session_id(captured)
    watchdog.record_invocation_start()
    # Manually drive the fire path with a non-resumable reason. The
    # watchdog's internal ``_evaluate_*`` paths route to specific
    # fire reasons, but the simplest way to test the typed
    # ``IdleWatchdogKilledError.resumable_session_id`` / merged_diag
    # ``resume_safe`` contract for a NON-resumable reason is to
    # directly call ``_check_fire`` with a forced fire reason on
    # the watchdog state.
    clock.advance(11.0)
    watchdog._last_fire_reason = WatchdogFireReason.CHILDREN_PERSIST_TOO_LONG

    # Build a synthetic FIRE verdict (the watchdog's evaluate path
    # already returned FIRE for the no-output scenario above; we
    # override ``_last_fire_reason`` to simulate a different reason
    # landing on the fire path -- the resume-safe contract must
    # still hold for the canonical reason set).
    verdict = WatchdogVerdict.FIRE
    # Force the ``CHILDREN_PERSIST_TOO_LONG`` timeout by hand-crafting
    # a minimal ``_IdleStreamTimeoutError`` for the fire reason --
    # but the simplest path is to set the watchdog's ``last_fire_reason``
    # and drive ``_check_fire`` through its natural logic.
    fire_result = reader._check_fire(watchdog, verdict)
    assert fire_result is not None, (
        "_check_fire MUST return a fire result for the test scenario"
    )
    _pending_lines, wrapper = fire_result
    diag = wrapper.diagnostic
    assert isinstance(diag, dict)
    # For ``CHILDREN_PERSIST_TOO_LONG`` ``resume_safe`` MUST be
    # False so the recovery controller does not emit a resume intent.
    assert diag.get("resume_safe") is False, (
        f"merged_diag.resume_safe MUST be False for CHILDREN_PERSIST_TOO_LONG;"
        f" got {diag.get('resume_safe')!r}"
    )

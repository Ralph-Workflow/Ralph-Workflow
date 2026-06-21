"""Black-box tests for the repeated-error/progress repetition tracker.

The tracker is the core of the circuit breaker that aborts an agent stuck
re-emitting the same error (e.g. ``MCP error -32001: Request timed out``)
forever. It must:

- fingerprint messages so per-occurrence noise (epoch timestamps, UUIDs, hex
  ids) collapses while stable signal (error codes like ``-32001``) survives,
- trip on N consecutive identical fingerprints with no intervening progress,
- trip on M occurrences of one fingerprint within a rolling window even when
  cosmetic output interleaves (so the consecutive streak keeps resetting),
- never trip on genuinely distinct messages, and
- be fully disablable.

Two independent repetition dimensions share the same consecutive + window
thresholds:

- the **error / cosmetic-progress** dimension via ``note_error``,
- the **tool-call** dimension via ``mark_tool_call`` (NEW).

The two dimensions track independent fingerprint deques so a real
error-loop and a real tool-call-loop can co-exist without cancelling each
other.

All timing is driven by an injected ``FakeClock`` so the suite stays well
within the 60s combined budget with zero real waits.
"""

from __future__ import annotations

from ralph.agents.idle_watchdog.repetition_tracker import RepetitionTracker
from ralph.agents.timeout_clock import FakeClock


def _tracker(
    clock: FakeClock,
    *,
    consecutive_threshold: int | None = 5,
    window_count: int | None = 8,
    window_seconds: float | None = 600.0,
) -> RepetitionTracker:
    return RepetitionTracker(
        clock,
        consecutive_threshold=consecutive_threshold,
        window_count=window_count,
        window_seconds=window_seconds,
    )


def test_fingerprint_preserves_error_code_but_strips_per_occurrence_noise() -> None:
    fp_a = RepetitionTracker.fingerprint("2026-06-08T12:21:32 MCP error -32001: Request timed out")
    fp_b = RepetitionTracker.fingerprint("2026-06-08T13:50:34 MCP error -32001: Request timed out")
    # Same underlying error, different timestamps -> identical fingerprint.
    assert fp_a == fp_b
    # The error code is signal and must survive normalization.
    assert "32001" in fp_a
    # A genuinely different error code must NOT collide.
    assert RepetitionTracker.fingerprint("MCP error -32603: Internal error") != fp_a


def test_fingerprint_collapses_uuid_and_epoch_noise() -> None:
    fp_a = RepetitionTracker.fingerprint(
        "tool call 1f0c8e2a-3b4d-4e5f-8a9b-0c1d2e3f4a5b failed at 1780921474"
    )
    fp_b = RepetitionTracker.fingerprint(
        "tool call 9a8b7c6d-5e4f-4a3b-2c1d-0e9f8a7b6c5d failed at 1780999999"
    )
    assert fp_a == fp_b


def test_n_consecutive_identical_errors_trips() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=5)
    msg = "MCP error -32001: Request timed out"
    for _ in range(4):
        tracker.note_error(msg)
        clock.advance(34.0)
        assert not tracker.tripped()
    tracker.note_error(msg)
    assert tracker.tripped()


def test_real_progress_resets_consecutive_streak() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=5)
    msg = "MCP error -32001: Request timed out"
    for _ in range(4):
        tracker.note_error(msg)
        clock.advance(34.0)
    tracker.note_progress()  # genuine forward progress breaks the streak
    for _ in range(4):
        tracker.note_error(msg)
        clock.advance(34.0)
        assert not tracker.tripped()


def test_real_progress_resets_tool_call_dimension() -> None:
    """``note_progress`` must clear the tool-call dimension too.

    Regression for a bug where genuine forward progress reset the error
    dimension but left identical tool-call fingerprints intact, causing a
    false-positive ``REPEATED_IDENTICAL_TOOL_CALL`` across a progress
    boundary.
    """
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=3, window_count=None, window_seconds=None)
    for _ in range(3):
        tracker.mark_tool_call("Bash", {"command": "ls"})
        clock.advance(1.0)
    assert tracker.tripped()
    assert tracker.tripped_tool_dimension()
    tracker.note_progress()  # genuine forward progress must clear both dimensions
    # One identical call after progress is not enough to trip again.
    tracker.mark_tool_call("Bash", {"command": "ls"})
    assert not tracker.tripped()
    assert not tracker.tripped_tool_dimension()
    # Rebuild the full consecutive streak to confirm the threshold still works.
    for _ in range(2):
        tracker.mark_tool_call("Bash", {"command": "ls"})
        clock.advance(1.0)
    assert tracker.tripped()
    assert tracker.tripped_tool_dimension()


def test_window_rule_trips_even_when_consecutive_streak_keeps_resetting() -> None:
    clock = FakeClock()
    # Disable the consecutive rule so only the window rule can trip.
    tracker = _tracker(clock, consecutive_threshold=None, window_count=8, window_seconds=600.0)
    timeout = "MCP error -32001: Request timed out"
    noise = "MCP error -32099: transient blip"
    for _ in range(8):
        tracker.note_error(timeout)
        tracker.note_error(noise)  # interleaved different fingerprint
        clock.advance(20.0)
    # 8 occurrences of the timeout fingerprint within the 600s window.
    assert tracker.tripped()


def test_distinct_messages_never_trip() -> None:
    clock = FakeClock()
    tracker = _tracker(clock)
    for index in range(20):
        tracker.note_error(f"distinct failure number {index} on file_{index}.py")
        clock.advance(5.0)
        assert not tracker.tripped()


def test_window_rule_does_not_trip_when_occurrences_age_out() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=None, window_count=8, window_seconds=600.0)
    msg = "MCP error -32001: Request timed out"
    for _ in range(20):
        tracker.note_error(msg)
        clock.advance(100.0)  # 100s apart -> at most 6 fit in a 600s window
        assert not tracker.tripped()


def test_disabled_tracker_never_trips() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=None, window_count=None, window_seconds=None)
    msg = "MCP error -32001: Request timed out"
    for _ in range(100):
        tracker.note_error(msg)
        clock.advance(1.0)
    assert not tracker.tripped()


# ---------------------------------------------------------------------------
# Tool-call dimension (NEW in this PR).
# ---------------------------------------------------------------------------


def test_mark_tool_call_fingerprints_tool_name_args() -> None:
    """``mark_tool_call`` MUST build a fingerprint on ``(tool_name, tool_args)``.

    Two calls with the same tool name and same arguments produce the
    same fingerprint (so the consecutive + window rules can trip on
    identical re-issues).  Reordering dict keys MUST NOT affect the
    fingerprint (sort_keys=True).
    """
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=2, window_count=None, window_seconds=None)
    tracker.mark_tool_call("Bash", {"command": "ls"})
    tracker.mark_tool_call("Bash", {"command": "ls"})
    # Two identical fingerprints -> consecutive streak of 2 = threshold.
    assert tracker.tripped()
    assert tracker.tripped_tool_dimension()


def test_mark_tool_call_key_order_does_not_affect_fingerprint() -> None:
    """Reordering dict keys in tool_args MUST NOT affect the fingerprint."""
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=2, window_count=None, window_seconds=None)
    tracker.mark_tool_call("Bash", {"command": "ls", "workdir": "/tmp"})
    tracker.mark_tool_call("Bash", {"workdir": "/tmp", "command": "ls"})
    assert tracker.tripped_tool_dimension()


def test_tripped_returns_true_after_repeated_identical_tool_calls() -> None:
    """The tool-call dimension trips after N consecutive identical
    ``mark_tool_call`` observations.

    The error dimension is NOT tripped (no ``note_error`` calls) so
    ``tripped_tool_dimension`` MUST return True so the watchdog can
    emit ``REPEATED_IDENTICAL_TOOL_CALL``.
    """
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=3, window_count=None, window_seconds=None)
    for _ in range(2):
        tracker.mark_tool_call("Bash", {"command": "ls"})
        clock.advance(1.0)
        assert not tracker.tripped()
    tracker.mark_tool_call("Bash", {"command": "ls"})
    assert tracker.tripped()
    # The error dimension is empty so tripped_tool_dimension returns True.
    assert tracker.tripped_tool_dimension()


def test_different_tool_call_args_do_not_trip() -> None:
    """Two tool calls with the same name but different args MUST NOT trip.

    ``Bash:ls`` and ``Bash:cat foo.txt`` are different tool calls; the
    tracker MUST NOT collapse them into a single fingerprint.
    """
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=2, window_count=None, window_seconds=None)
    tracker.mark_tool_call("Bash", {"command": "ls"})
    tracker.mark_tool_call("Bash", {"command": "cat foo.txt"})
    assert not tracker.tripped()
    assert not tracker.tripped_tool_dimension()


def test_tool_call_window_rule_trips_with_consecutive_streak_resets() -> None:
    """The window rule trips on the tool-call dimension too: when a tiny
    bit of cosmetic different-tool-call output interleaves with the
    identical-tool-call wedge, the consecutive streak keeps resetting
    but the window rule still trips.
    """
    clock = FakeClock()
    # Disable consecutive so only the window rule can trip.
    tracker = _tracker(clock, consecutive_threshold=None, window_count=4, window_seconds=600.0)
    for _ in range(4):
        tracker.mark_tool_call("Bash", {"command": "ls"})
        tracker.mark_tool_call("Read", {"path": "/tmp/other.txt"})
        clock.advance(20.0)
    # 4 occurrences of the Bash:ls fingerprint within the 600s window.
    assert tracker.tripped()
    assert tracker.tripped_tool_dimension()


def test_error_and_tool_dimensions_are_independent() -> None:
    """Tripping the tool-call dimension MUST NOT trip the error dimension.

    The two dimensions are tracked in separate deques so a real
    tool-call wedge can be detected without a coincident error-loop.
    """
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=3, window_count=None, window_seconds=None)
    # Three identical tool calls trip the tool-call dimension.
    for _ in range(3):
        tracker.mark_tool_call("Bash", {"command": "ls"})
        clock.advance(1.0)
    assert tracker.tripped()
    # tripped_tool_dimension returns True ONLY for the tool-call
    # dimension.  Confirm the error dimension is empty.
    assert tracker.tripped_tool_dimension()


def test_error_dimension_takes_precedence_over_tool_dimension() -> None:
    """When BOTH dimensions are tripped the watchdog fires
    ``REPEATED_ERROR_LOOP`` so the canonical reason wins.

    ``tripped_tool_dimension`` returns False in that case (the
    error reason wins for routing purposes).  ``tripped`` returns
    True so the watchdog fires.
    """
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=3, window_count=None, window_seconds=None)
    msg = "MCP error -32001: Request timed out"
    for _ in range(3):
        tracker.note_error(msg)
        tracker.mark_tool_call("Bash", {"command": "ls"})
        clock.advance(1.0)
    assert tracker.tripped()
    # Both dimensions tripped -> tool dimension is NOT the one that
    # should drive the fire reason (error dimension wins).
    assert not tracker.tripped_tool_dimension()

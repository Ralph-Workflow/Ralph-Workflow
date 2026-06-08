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
    fp_a = RepetitionTracker.fingerprint(
        "2026-06-08T12:21:32 MCP error -32001: Request timed out"
    )
    fp_b = RepetitionTracker.fingerprint(
        "2026-06-08T13:50:34 MCP error -32001: Request timed out"
    )
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


def test_window_rule_trips_even_when_consecutive_streak_keeps_resetting() -> None:
    clock = FakeClock()
    # Disable the consecutive rule so only the window rule can trip.
    tracker = _tracker(
        clock, consecutive_threshold=None, window_count=8, window_seconds=600.0
    )
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
    tracker = _tracker(
        clock, consecutive_threshold=None, window_count=8, window_seconds=600.0
    )
    msg = "MCP error -32001: Request timed out"
    for _ in range(20):
        tracker.note_error(msg)
        clock.advance(100.0)  # 100s apart -> at most 6 fit in a 600s window
        assert not tracker.tripped()


def test_disabled_tracker_never_trips() -> None:
    clock = FakeClock()
    tracker = _tracker(
        clock, consecutive_threshold=None, window_count=None, window_seconds=None
    )
    msg = "MCP error -32001: Request timed out"
    for _ in range(100):
        tracker.note_error(msg)
        clock.advance(1.0)
    assert not tracker.tripped()

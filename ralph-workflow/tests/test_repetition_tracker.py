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
    assert fp_a == fp_b
    assert "32001" in fp_a
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
    tracker = _tracker(clock, consecutive_threshold=5, window_count=None, window_seconds=None)
    msg = "MCP error -32001: Request timed out"
    for _ in range(4):
        tracker.note_error(msg)
        clock.advance(34.0)
    tracker.note_progress()
    for _ in range(4):
        tracker.note_error(msg)
        clock.advance(34.0)
        assert not tracker.tripped()


def test_window_rule_trips_when_forward_progress_interleaves_errors() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=None, window_count=8, window_seconds=600.0)
    for _ in range(8):
        tracker.note_error("fetch failed")
        tracker.note_progress()
        clock.advance(20.0)
    assert tracker.tripped()


def test_window_rule_trips_on_tool_dimension_when_progress_interleaves() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=None, window_count=4, window_seconds=600.0)
    for _ in range(4):
        tracker.mark_tool_call("list_directory", {"path": "."})
        tracker.note_progress()
        clock.advance(20.0)
    assert tracker.tripped()
    assert tracker.tripped_tool_dimension()


def test_progress_does_not_extend_the_window_lifetime() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=None, window_count=3, window_seconds=100.0)
    for _ in range(3):
        tracker.note_error("fetch failed")
        tracker.note_progress()
        clock.advance(50.0)
    assert not tracker.tripped()


def test_real_progress_resets_tool_call_dimension() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=3, window_count=None, window_seconds=None)
    for _ in range(3):
        tracker.mark_tool_call("Bash", {"command": "ls"})
        clock.advance(1.0)
    assert tracker.tripped()
    assert tracker.tripped_tool_dimension()
    tracker.note_progress()
    tracker.mark_tool_call("Bash", {"command": "ls"})
    assert not tracker.tripped()
    assert not tracker.tripped_tool_dimension()
    for _ in range(2):
        tracker.mark_tool_call("Bash", {"command": "ls"})
        clock.advance(1.0)
    assert tracker.tripped()
    assert tracker.tripped_tool_dimension()


def test_window_rule_trips_even_when_consecutive_streak_keeps_resetting() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=None, window_count=8, window_seconds=600.0)
    timeout = "MCP error -32001: Request timed out"
    noise = "MCP error -32099: transient blip"
    for _ in range(8):
        tracker.note_error(timeout)
        tracker.note_error(noise)
        clock.advance(20.0)
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
        clock.advance(100.0)
        assert not tracker.tripped()


def test_disabled_tracker_never_trips() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=None, window_count=None, window_seconds=None)
    msg = "MCP error -32001: Request timed out"
    for _ in range(100):
        tracker.note_error(msg)
        clock.advance(1.0)
    assert not tracker.tripped()


def test_mark_tool_call_fingerprints_tool_name_args() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=2, window_count=None, window_seconds=None)
    tracker.mark_tool_call("Bash", {"command": "ls"})
    tracker.mark_tool_call("Bash", {"command": "ls"})
    assert tracker.tripped()
    assert tracker.tripped_tool_dimension()


def test_mark_tool_call_key_order_does_not_affect_fingerprint() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=2, window_count=None, window_seconds=None)
    tracker.mark_tool_call("Bash", {"command": "ls", "workdir": "/tmp"})
    tracker.mark_tool_call("Bash", {"workdir": "/tmp", "command": "ls"})
    assert tracker.tripped_tool_dimension()


def test_tripped_returns_true_after_repeated_identical_tool_calls() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=3, window_count=None, window_seconds=None)
    for _ in range(2):
        tracker.mark_tool_call("Bash", {"command": "ls"})
        clock.advance(1.0)
        assert not tracker.tripped()
    tracker.mark_tool_call("Bash", {"command": "ls"})
    assert tracker.tripped()
    assert tracker.tripped_tool_dimension()


def test_different_tool_call_args_do_not_trip() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=2, window_count=None, window_seconds=None)
    tracker.mark_tool_call("Bash", {"command": "ls"})
    tracker.mark_tool_call("Bash", {"command": "cat foo.txt"})
    assert not tracker.tripped()
    assert not tracker.tripped_tool_dimension()


def test_tool_call_window_rule_trips_with_consecutive_streak_resets() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=None, window_count=4, window_seconds=600.0)
    for _ in range(4):
        tracker.mark_tool_call("Bash", {"command": "ls"})
        tracker.mark_tool_call("Read", {"path": "/tmp/other.txt"})
        clock.advance(20.0)
    assert tracker.tripped()
    assert tracker.tripped_tool_dimension()


def test_error_and_tool_dimensions_are_independent() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=3, window_count=None, window_seconds=None)
    for _ in range(3):
        tracker.mark_tool_call("Bash", {"command": "ls"})
        clock.advance(1.0)
    assert tracker.tripped()
    assert tracker.tripped_tool_dimension()


def test_error_dimension_takes_precedence_over_tool_dimension() -> None:
    clock = FakeClock()
    tracker = _tracker(clock, consecutive_threshold=3, window_count=None, window_seconds=None)
    msg = "MCP error -32001: Request timed out"
    for _ in range(3):
        tracker.note_error(msg)
        tracker.mark_tool_call("Bash", {"command": "ls"})
        clock.advance(1.0)
    assert tracker.tripped()
    assert not tracker.tripped_tool_dimension()


def test_event_buffer_maxlen_is_derived_from_window_count() -> None:
    clock = FakeClock()
    tracker_small = RepetitionTracker(
        clock,
        consecutive_threshold=None,
        window_count=5,
        window_seconds=None,
    )
    assert tracker_small.event_buffer_maxlen == 256

    tracker_mid = RepetitionTracker(
        clock,
        consecutive_threshold=None,
        window_count=64,
        window_seconds=None,
    )
    assert tracker_mid.event_buffer_maxlen == 512

    tracker_disabled = RepetitionTracker(
        clock,
        consecutive_threshold=None,
        window_count=None,
        window_seconds=None,
    )
    assert tracker_disabled.event_buffer_maxlen == 256


def test_event_deque_does_not_grow_unbounded_with_window_seconds_disabled() -> None:
    clock = FakeClock()
    tracker = RepetitionTracker(
        clock,
        consecutive_threshold=None,
        window_count=5,
        window_seconds=None,
    )
    msg = "MCP error -32001: Request timed out"
    for _ in range(5000):
        tracker.note_error(msg)
    assert len(tracker._events) <= tracker.event_buffer_maxlen
    assert len(tracker._events) == tracker.event_buffer_maxlen, (
        f"deque should be exactly at cap ({tracker.event_buffer_maxlen})"
        f" after 5000 appends, got len={len(tracker._events)}"
    )


def test_tool_event_deque_does_not_grow_unbounded_with_window_seconds_disabled() -> None:
    clock = FakeClock()
    tracker = RepetitionTracker(
        clock,
        consecutive_threshold=None,
        window_count=5,
        window_seconds=None,
    )
    for _ in range(5000):
        tracker.mark_tool_call("Bash", {"command": "ls"})
    assert len(tracker._tool_events) <= tracker.event_buffer_maxlen
    assert len(tracker._tool_events) == tracker.event_buffer_maxlen, (
        f"tool-event deque should be at cap ({tracker.event_buffer_maxlen})"
        f" after 5000 appends, got len={len(tracker._tool_events)}"
    )


def test_consecutive_rule_still_trips_with_window_disabled_and_bounded_deque() -> None:
    clock = FakeClock()
    tracker = RepetitionTracker(
        clock,
        consecutive_threshold=5,
        window_count=5,
        window_seconds=None,
    )
    msg = "MCP error -32001: Request timed out"
    for _ in range(4):
        tracker.note_error(msg)
    assert not tracker.tripped()
    tracker.note_error(msg)
    assert tracker.tripped()
    for _ in range(5000):
        tracker.note_error(msg)
    assert tracker.tripped()


def test_window_rule_fires_with_bounded_deque_when_window_enabled() -> None:
    clock = FakeClock()
    tracker = RepetitionTracker(
        clock,
        consecutive_threshold=None,
        window_count=8,
        window_seconds=600.0,
    )
    msg = "MCP error -32001: Request timed out"
    for _ in range(8):
        tracker.note_error(msg)
        clock.advance(10.0)
    assert tracker.tripped()
    assert len(tracker._events) == 8


def test_note_progress_preserves_bounded_window_evidence() -> None:
    clock = FakeClock()
    tracker = RepetitionTracker(
        clock,
        consecutive_threshold=None,
        window_count=5,
        window_seconds=None,
    )
    for _ in range(tracker.event_buffer_maxlen + 100):
        tracker.note_error("MCP error -32001: Request timed out")
    assert len(tracker._events) == tracker.event_buffer_maxlen
    tracker.note_progress()
    assert len(tracker._events) == tracker.event_buffer_maxlen
    assert tracker.event_buffer_maxlen == 256

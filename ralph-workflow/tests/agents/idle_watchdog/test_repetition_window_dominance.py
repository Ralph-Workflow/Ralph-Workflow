"""A repeated tool call only counts as a wedge when it DOMINATES its window.

The window rule exists so cosmetic OUTPUT interleaved between identical calls
cannot reset the streak. It was never meant to survive interleaved *other tool
calls*: an agent reaching for eight different tools between repeats is working,
not wedged. Counting 8 occurrences of one fingerprint anywhere in the trailing
600s killed ordinary habits -- ``git status`` after each edit, re-reading the
plan draft, polling a build. One captured healthy pi run issues
``mcp__ralph__ralph_get_plan_draft`` 14 times among ~250 tool calls.
"""

from __future__ import annotations

from ralph.agents.idle_watchdog.repetition_tracker import RepetitionTracker
from ralph.agents.timeout_clock import FakeClock


def _tracker(clock: FakeClock) -> RepetitionTracker:
    return RepetitionTracker(
        clock,
        consecutive_threshold=5,
        window_count=8,
        window_seconds=600.0,
    )


def test_repeats_diluted_by_real_work_do_not_trip() -> None:
    """14 identical polls among ~250 distinct calls is 6% of the window."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for index in range(250):
        tracker.mark_tool_call("ralph_read_file", {"path": f"/repo/file_{index}.py"})
        clock.advance(1.0)
        if index % 18 == 0:
            tracker.mark_tool_call("ralph_get_plan_draft", {})
            clock.advance(1.0)

    assert not tracker.tripped_tool_dimension()


def test_repeats_dominating_the_window_still_trip() -> None:
    """A wedge is ~100% of its window and MUST still be caught."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(8):
        tracker.mark_tool_call("ralph_exec", {"command": "uv run pytest -q"})
        clock.advance(1.0)

    assert tracker.tripped_tool_dimension()


def test_two_call_alternating_loop_still_trips() -> None:
    """An A/B/A/B loop is 50% of its window, which is still a wedge."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(8):
        tracker.mark_tool_call("ralph_exec", {"command": "make test"})
        clock.advance(1.0)
        tracker.mark_tool_call("ralph_read_file", {"path": "/repo/out.log"})
        clock.advance(1.0)

    assert tracker.tripped_tool_dimension()


def test_interleaved_output_still_trips() -> None:
    """The rule's original purpose survives: text between repeats is not work.

    ``note_progress`` resets only the consecutive streak, so the window must
    still accumulate when the ONLY thing between repeats is ordinary output.
    """
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(8):
        tracker.mark_tool_call("ralph_exec", {"command": "make test"})
        tracker.note_progress()
        clock.advance(1.0)

    assert tracker.tripped_tool_dimension()

"""A repeated tool call only counts as a wedge when the window is a CYCLE.

The window rule exists so cosmetic OUTPUT interleaved between identical calls
cannot reset the streak. It was never meant to survive interleaved *other tool
calls*: repeating one call while otherwise doing varied work is how agents poll
a build or re-check ``git status``, not how they wedge. Counting 8 occurrences
of one fingerprint anywhere in the trailing 600s killed those habits -- one
captured healthy pi run issues ``mcp__ralph__ralph_get_plan_draft`` 14 times
among 124 tool calls.

Diversity is the discriminator, not share-of-window: a share test set at half
the window lets an unambiguous three-call A/B/C loop sit at 33% and escape.
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


def test_polling_threaded_through_varied_work_does_not_trip() -> None:
    """The real pi shape: 14 identical plan-draft reads among 124 calls."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for index in range(124):
        tracker.mark_tool_call("ralph_read_file", {"path": f"/repo/file_{index}.py"})
        clock.advance(1.0)
        if index % 9 == 0:
            tracker.mark_tool_call("ralph_get_plan_draft", {})
            clock.advance(1.0)

    assert not tracker.tripped_tool_dimension()


def test_distinct_calls_at_run_start_do_not_trip() -> None:
    """A small window early in a run must not be read as a cycle."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for index in range(8):
        tracker.mark_tool_call("ralph_read_file", {"path": f"/repo/{index}.py"})
        clock.advance(1.0)

    assert not tracker.tripped_tool_dimension()


def test_one_call_repeated_still_trips() -> None:
    """The canonical wedge: the same call and nothing else."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(8):
        tracker.mark_tool_call("ralph_exec", {"command": "uv run pytest -q"})
        clock.advance(1.0)

    assert tracker.tripped_tool_dimension()


def test_two_call_loop_still_trips() -> None:
    """An A/B/A/B loop is a wedge with two moving parts."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(10):
        tracker.mark_tool_call("ralph_exec", {"command": "make test"})
        clock.advance(1.0)
        tracker.mark_tool_call("ralph_read_file", {"path": "/repo/out.log"})
        clock.advance(1.0)

    assert tracker.tripped_tool_dimension()


def test_three_call_loop_still_trips() -> None:
    """A/B/C is still a cycle; a share-of-window test would miss it at 33%."""
    clock = FakeClock()
    tracker = _tracker(clock)

    for _ in range(9):
        for name in ("ralph_exec", "ralph_read_file", "ralph_git_status"):
            tracker.mark_tool_call(name, {})
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

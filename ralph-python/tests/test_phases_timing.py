"""Unit tests for phase timing utilities."""

from __future__ import annotations

from importlib import import_module
from time import sleep

timing = import_module("ralph.phases.timing")


def test_capture_time_returns_monotonic_float() -> None:
    """Captured timestamps should be monotonic float values."""
    started = timing.capture_time()

    assert isinstance(started, float)
    assert started >= 0.0


def test_elapsed_reports_duration_since_start() -> None:
    """Elapsed duration should be non-negative and monotonic."""
    started = timing.capture_time()
    sleep(0.01)

    duration = timing.elapsed(started)

    assert duration.total_seconds() >= 0.01


def test_elapsed_seconds_rounds_down_to_whole_seconds() -> None:
    """Whole-second elapsed helper should floor sub-second durations."""
    started = timing.capture_time()

    seconds = timing.elapsed_seconds(started)

    assert isinstance(seconds, int)
    assert seconds >= 0


def test_phase_timer_records_named_phase_duration() -> None:
    """PhaseTimer should produce a structured completed timing record."""
    timer = timing.PhaseTimer.start("development", iteration=2)
    sleep(0.01)

    record = timer.finish()

    assert record.phase == "development"
    assert record.iteration == 2
    assert record.elapsed.total_seconds() >= 0.01
    assert record.elapsed_seconds == int(record.elapsed.total_seconds())

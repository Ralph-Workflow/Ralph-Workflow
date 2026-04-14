"""Unit tests for phase timing utilities."""

from __future__ import annotations

from importlib import import_module
from time import sleep

timing = import_module("ralph.phases.timing")


MIN_ELAPSED_SECONDS = 0.01
ITERATION_NUMBER = 2


def test_capture_time_returns_monotonic_float() -> None:
    """Captured timestamps should be monotonic float values."""
    started = timing.capture_time()

    assert isinstance(started, float)
    assert started >= 0.0


def test_elapsed_reports_duration_since_start() -> None:
    """Elapsed duration should be non-negative and monotonic."""
    started = timing.capture_time()
    sleep(MIN_ELAPSED_SECONDS)

    duration = timing.elapsed(started)

    assert duration.total_seconds() >= MIN_ELAPSED_SECONDS


def test_elapsed_seconds_rounds_down_to_whole_seconds() -> None:
    """Whole-second elapsed helper should floor sub-second durations."""
    started = timing.capture_time()

    seconds = timing.elapsed_seconds(started)

    assert isinstance(seconds, int)
    assert seconds >= 0


def test_phase_timer_records_named_phase_duration() -> None:
    """PhaseTimer should produce a structured completed timing record."""
    timer = timing.PhaseTimer.start("development", iteration=ITERATION_NUMBER)
    sleep(MIN_ELAPSED_SECONDS)

    record = timer.finish()

    assert record.phase == "development"
    assert record.iteration == ITERATION_NUMBER
    assert record.elapsed.total_seconds() >= MIN_ELAPSED_SECONDS
    assert record.elapsed_seconds == int(record.elapsed.total_seconds())

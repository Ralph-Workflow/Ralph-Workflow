"""Unit tests for phase timing utilities."""

from __future__ import annotations

from importlib import import_module
from typing import TYPE_CHECKING

timing = import_module("ralph.phases.timing")

if TYPE_CHECKING:
    import pytest


START_TIME = 100.0
ELAPSED_TIME = 0.25
ITERATION_NUMBER = 2


def test_capture_time_returns_monotonic_float() -> None:
    """Captured timestamps should be monotonic float values."""
    started = timing.capture_time()

    assert isinstance(started, float)
    assert started >= 0.0


def test_elapsed_reports_duration_since_start(monkeypatch: pytest.MonkeyPatch) -> None:
    """Elapsed duration should be non-negative and monotonic."""
    monkeypatch.setattr(timing, "monotonic", lambda: START_TIME + ELAPSED_TIME)

    duration = timing.elapsed(START_TIME)

    assert duration.total_seconds() == ELAPSED_TIME


def test_elapsed_seconds_rounds_down_to_whole_seconds(monkeypatch: pytest.MonkeyPatch) -> None:
    """Whole-second elapsed helper should floor sub-second durations."""
    monkeypatch.setattr(timing, "monotonic", lambda: START_TIME + 0.99)

    seconds = timing.elapsed_seconds(START_TIME)

    assert isinstance(seconds, int)
    assert seconds == 0


def test_phase_timer_records_named_phase_duration(monkeypatch: pytest.MonkeyPatch) -> None:
    """PhaseTimer should produce a structured completed timing record."""
    times = iter([START_TIME, START_TIME + ELAPSED_TIME])
    monkeypatch.setattr(timing, "capture_time", lambda: next(times))
    monkeypatch.setattr(timing, "monotonic", lambda: START_TIME + ELAPSED_TIME)

    timer = timing.PhaseTimer.start("development", iteration=ITERATION_NUMBER)
    record = timer.finish()

    assert record.phase == "development"
    assert record.iteration == ITERATION_NUMBER
    assert record.elapsed.total_seconds() == ELAPSED_TIME
    assert record.elapsed_seconds == int(record.elapsed.total_seconds())

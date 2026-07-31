"""Behavior tests for the bounded, comparable run-time report."""

from datetime import timedelta

from ralph.phases.phase_timing_record import PhaseTimingRecord
from ralph.pipeline.run_time_report import render_run_time_report
from ralph.pipeline.state import PipelineState


def _timing(phase: str, elapsed_seconds: int, iteration: int = 0) -> PhaseTimingRecord:
    return PhaseTimingRecord(
        phase=phase,
        iteration=iteration,
        started_at=0.0,
        elapsed=timedelta(seconds=elapsed_seconds),
        elapsed_seconds=elapsed_seconds,
    )


def test_run_time_report_has_stable_sections_and_real_metrics() -> None:
    report = render_run_time_report(
        state=PipelineState(
            phase="development",
            phase_timings=(
                _timing("planning", 2),
                _timing("development", 5),
                _timing("development", 3, iteration=1),
            ),
        ),
        outcome="completed",
        elapsed_seconds=1.25,
    )

    assert "type: run_time_report" in report
    assert "elapsed_seconds: 1.250" in report
    assert "## Timing" in report
    assert "Agent-controlled time: unavailable" in report
    assert "Imposed time: unavailable" in report
    assert "## Phases" in report
    assert "planning: 2s" in report
    assert "development: 5s" in report
    assert "## Slowest Steps" in report
    assert "development: 5s" in report
    assert "## Signals" in report
    assert len(report) <= 1_600

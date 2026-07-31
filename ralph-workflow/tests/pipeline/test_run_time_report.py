"""Behavior tests for the bounded, comparable run-time report."""

from datetime import timedelta

from ralph.phases.phase_timing_record import PhaseTimingRecord
from ralph.pipeline.run_time_report import render_run_time_report
from ralph.pipeline.state import PipelineState, RunMetrics


def _timing(phase: str, elapsed_seconds: int, iteration: int = 0) -> PhaseTimingRecord:
    return PhaseTimingRecord(
        phase=phase,
        iteration=iteration,
        started_at=0.0,
        elapsed=timedelta(seconds=min(elapsed_seconds, 999_999_999)),
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


def test_run_time_report_regression_truncates_long_phase_names_within_budget() -> None:
    slowest_phase = "00-" + "x" * 67
    report = render_run_time_report(
        state=PipelineState(
            phase="development",
            phase_timings=tuple(
                _timing(f"{index:02d}-" + "x" * 67, 20 - index) for index in range(20)
            ),
        ),
        outcome="completed",
        elapsed_seconds=20,
    )

    assert len(report) <= 1_600
    assert f"{slowest_phase}: 20s" in report.split("## Slowest Steps", maxsplit=1)[1]
    assert "truncated to fit the reporting budget" in report


def test_run_time_report_regression_caps_extreme_values_without_losing_slowest_phase() -> None:
    slowest_phase = "y" * 80
    report = render_run_time_report(
        state=PipelineState(
            phase="x" * 80,
            phase_timings=(_timing(slowest_phase, 10**2_000),),
        ),
        outcome="z" * 80,
        elapsed_seconds=1e308,
    )

    assert len(report) <= 1_600
    slowest_steps = report.split("## Slowest Steps", maxsplit=1)[1]
    assert slowest_phase in slowest_steps
    assert "~1e2000s" in slowest_steps


def test_run_time_report_regression_preserves_negative_extreme_signal_counts() -> None:
    report = render_run_time_report(
        state=PipelineState(
            phase="development",
            metrics=RunMetrics(total_agent_calls=-(10**2_000)),
        ),
        outcome="completed",
        elapsed_seconds=1,
    )

    assert len(report) <= 1_600
    assert "Agent calls: -~1e2000" in report

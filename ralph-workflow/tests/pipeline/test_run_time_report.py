"""Behavior tests for the bounded, comparable run-time report."""

from ralph.pipeline.run_time_report import render_run_time_report
from ralph.pipeline.state import PipelineState


def test_run_time_report_has_stable_sections_and_real_metrics() -> None:
    report = render_run_time_report(
        state=PipelineState(phase="development"), outcome="completed", elapsed_seconds=1.25
    )

    assert "type: run_time_report" in report
    assert "elapsed_seconds: 1.250" in report
    assert "## Timing" in report
    assert "## Phases" in report
    assert "## Slowest Steps" in report
    assert "## Signals" in report
    assert "no invented timing is reported" in report

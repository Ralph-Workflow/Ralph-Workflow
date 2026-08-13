"""Behavior tests for the bounded, comparable run-time report."""

from datetime import timedelta

from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec
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


def test_run_time_report_regression_memory_findings_are_optional_and_validated() -> None:
    state = PipelineState(phase="development")
    baseline = render_run_time_report(state=state, outcome="completed", elapsed_seconds=1)
    without_findings = render_run_time_report(
        state=state,
        outcome="completed",
        elapsed_seconds=1,
        getenv=lambda _key: None,
    )
    report = render_run_time_report(
        state=state,
        outcome="completed",
        elapsed_seconds=1,
        getenv=lambda _key: "bounded buffers verified\n\nlate phases held steady",
    )

    assert baseline == without_findings
    assert "## Memory Findings" not in baseline
    assert "## Memory Findings" in report
    assert "- [MF-1] bounded buffers verified" in report
    assert "- [MF-2] late phases held steady" in report
    assert len(report) <= 1_600
    __import__("ralph.mcp.artifacts.markdown.specs")
    _, diagnostics = parse_and_validate(report, get_spec("run_time_report"))
    assert not [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]


def test_run_time_report_regression_trims_clamped_memory_findings_for_validation() -> None:
    report = render_run_time_report(
        state=PipelineState(phase="development"),
        outcome="completed",
        elapsed_seconds=1,
        getenv=lambda _key: "x" * 79 + " trailing text",
    )

    __import__("ralph.mcp.artifacts.markdown.specs")
    _, diagnostics = parse_and_validate(report, get_spec("run_time_report"))
    assert not [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]


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


def test_run_time_report_includes_cycle_timebox_when_consumed() -> None:
    """The report shows cycle timebox diagnostics when budget was consumed."""
    report = render_run_time_report(
        state=PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=3600.0,
        ),
        outcome="completed",
        elapsed_seconds=3600.0,
    )
    assert "## Cycle Timebox" in report
    assert "[CT-1]" in report
    assert "3600" in report
    assert "active" in report
    assert len(report) <= 1_600


def test_run_time_report_omits_cycle_timebox_when_unused() -> None:
    """No cycle timebox section when the budget was never consumed."""
    report = render_run_time_report(
        state=PipelineState(phase="planning"),
        outcome="completed",
        elapsed_seconds=10.0,
    )
    assert "## Cycle Timebox" not in report


def test_run_time_report_shows_concluded_when_cycle_ended() -> None:
    """After the cycle ends, the report shows 'concluded' not 'active'."""
    report = render_run_time_report(
        state=PipelineState(
            phase="complete",
            cycle_timebox_active=False,
            cycle_timebox_consumed_seconds=7200.0,
        ),
        outcome="completed",
        elapsed_seconds=7200.0,
    )
    assert "## Cycle Timebox" in report
    assert "concluded" in report


def test_run_time_report_shows_redirect_reason_when_set() -> None:
    """When redirect_reason is set, the report shows a CT-2 redirect line."""
    report = render_run_time_report(
        state=PipelineState(
            phase="development_final_commit_cleanup",
            cycle_timebox_active=False,
            cycle_timebox_consumed_seconds=7200.0,
            cycle_timebox_redirect_reason="cycle_deadline_expired",
        ),
        outcome="completed",
        elapsed_seconds=7200.0,
    )
    assert "[CT-1]" in report
    assert "[CT-2]" in report
    assert "cycle_deadline_expired" in report
    assert "concluded" in report


def test_run_time_report_shows_configured_limit_and_target_when_policy_given() -> None:
    """When the cycle_timebox policy is threaded in, the report includes the
    configured deadline and finalization target (FR-6, S-5)."""
    from ralph.policy.models import CycleTimeboxPolicy

    ct = CycleTimeboxPolicy(
        duration_seconds=7200.0,
        start_source="planning_analysis",
        start_entry="development",
        guarded_entry="development",
        end_entry="development_final_commit_cleanup",
        finalization_target="development_final_commit_cleanup",
    )
    report = render_run_time_report(
        state=PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=3600.0,
        ),
        outcome="completed",
        elapsed_seconds=3600.0,
        cycle_timebox=ct,
    )
    assert "## Cycle Timebox" in report
    assert "[CT-1]" in report
    assert "Limit: 7200" in report
    assert "consumed: 3600" in report
    assert "Finalization: development_final_commit_cleanup" in report
    assert len(report) <= 1_600

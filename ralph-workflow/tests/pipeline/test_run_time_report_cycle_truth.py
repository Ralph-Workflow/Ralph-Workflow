"""The cycle-timebox section must not tell the operator something untrue.

Three ways it did. It reported a run that died inside its cycle as "active",
describing a finished run as still running. It truncated the redirect reason at
the phase-name cap, and because the reason ends with the finalization target,
the operator was handed a phase name that does not exist. And it dropped the
entire section whenever consumed time was exactly zero — which is the state of
every cycle's first development step, so a run ending there reported no budget
at all while the live banner was showing one.
"""

from __future__ import annotations

from ralph.pipeline.cycle_timing import cycle_timebox_redirect_reason
from ralph.pipeline.run_time_report import render_run_time_report
from ralph.pipeline.state import PipelineState
from ralph.policy.models import CycleTimeboxPolicy

_TARGET = "development_final_commit_cleanup"


def _timebox() -> CycleTimeboxPolicy:
    return CycleTimeboxPolicy(
        duration_seconds=7200.0,
        start_source="planning_analysis",
        start_entry="development",
        guarded_entry="development",
        end_entry=_TARGET,
        finalization_target=_TARGET,
    )


def _report(state: PipelineState) -> str:
    return render_run_time_report(
        state=state,
        outcome="failed",
        elapsed_seconds=4321.0,
        getenv=lambda _name: None,
        cycle_timebox=_timebox(),
    )


def test_a_run_that_ended_inside_its_cycle_is_not_called_active() -> None:
    """The report is rendered after the run is over; nothing is still running.

    A run that exits from inside the cycle never reduces a route out of it, so
    the timer flag stays set — which the report printed verbatim as "active".
    """
    report = _report(
        PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=4321.0,
        )
    )

    assert "[CT-1]" in report
    assert " active." not in report


def test_the_redirect_reason_is_not_truncated_mid_token() -> None:
    """The reason names the phase the cycle was redirected to; a cut name lies."""
    reason = cycle_timebox_redirect_reason(
        limit_seconds=7200.0, elapsed_seconds=7205.0, target=_TARGET
    )
    report = _report(
        PipelineState(
            phase=_TARGET,
            cycle_timebox_active=False,
            cycle_timebox_consumed_seconds=7205.0,
            cycle_timebox_redirect_reason=reason,
        )
    )

    assert "[CT-2]" in report
    assert _TARGET in report.split("[CT-2]", 1)[1]


def test_a_cycle_that_has_consumed_no_time_yet_still_reports_its_budget() -> None:
    """Consumed is exactly zero for the whole first step of every cycle."""
    report = _report(
        PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=0.0,
        )
    )

    assert "## Cycle Timebox" in report
    assert "[CT-1]" in report


def test_a_run_that_never_entered_a_cycle_reports_no_section() -> None:
    """The section must not appear where no cycle ever ran."""
    report = _report(PipelineState(phase="planning"))

    assert "## Cycle Timebox" not in report


def test_a_redirect_in_an_earlier_cycle_is_still_reported() -> None:
    """A redirected cycle is normally followed by another, which resets the reason.

    Under the bundled defaults a timed-out cycle is finalized and the next
    planning cycle starts, so the reason survives only until the next cycle
    begins — the operator of a five-cycle run was never told that any of the
    first four blew their deadline.
    """
    report = _report(
        PipelineState(
            phase="development",
            cycle_timebox_active=True,
            cycle_timebox_consumed_seconds=1200.0,
            cycle_timebox_redirects=2,
        )
    )

    assert "[CT-2]" in report
    assert "2" in report.split("[CT-2]", 1)[1].split("\n", 1)[0]


def _timed(phase: str, seconds: int) -> object:
    from datetime import timedelta

    from ralph.phases.phase_timing_record import PhaseTimingRecord

    return PhaseTimingRecord(
        phase=phase,
        iteration=1,
        started_at=0.0,
        elapsed=timedelta(seconds=seconds),
        elapsed_seconds=seconds,
    )


def test_phases_dropped_by_the_cap_are_announced_in_both_sections() -> None:
    """Silently listing six of eleven phases reads as the complete list."""
    state = PipelineState(
        phase="development",
        phase_timings=tuple(_timed(f"phase_{index}", 100 + index) for index in range(11)),
    )

    report = render_run_time_report(
        state=state, outcome="completed", elapsed_seconds=1200.0, getenv=lambda _n: None
    )

    assert "[P-8]" in report
    assert "[SS-8]" in report


def test_dropping_every_timing_is_not_reported_as_recording_none() -> None:
    """"None were recorded" and "all were dropped" are different facts."""
    state = PipelineState(
        phase="development",
        # Long phase names plus eight max-length findings crowd the phase
        # list out of the 1600-character budget entirely.
        phase_timings=tuple(
            _timed(f"phase_{index}_" + "n" * 70, 100) for index in range(6)
        ),
    )

    report = render_run_time_report(
        state=state,
        outcome="completed",
        elapsed_seconds=1200.0,
        getenv=lambda _n: "\n".join("f" * 80 for _ in range(8)),
    )

    assert "No completed phase timings were recorded" not in report
    assert "dropped to fit the budget" in report


def test_dropped_memory_findings_are_announced() -> None:
    """A findings list that quietly loses entries reads as the complete set."""
    report = render_run_time_report(
        state=PipelineState(phase="development"),
        outcome="completed",
        elapsed_seconds=1200.0,
        # More findings than the cap allows.
        getenv=lambda _n: "\n".join(f"finding {index}" for index in range(12)),
    )

    assert "Memory findings list truncated" in report

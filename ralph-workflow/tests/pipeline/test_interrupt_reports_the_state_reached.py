"""An interrupted run must be checkpointed and reported where it actually was.

``KeyboardInterrupt`` unwinds out of the inner loop past every state that loop
had reached, leaving the caller holding the state the run STARTED with. The
checkpoint, the end-of-run report and the cleanup all read that binding, so a
run interrupted after hours in development was recorded as having ended in
planning — no phases, no signals, no cycle timebox section.
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ralph.config.enums import Verbosity
from ralph.pipeline import run_loop
from ralph.pipeline import runner as runner_module
from ralph.pipeline.state import PipelineState

_INTERRUPT_EXIT_CODE = 130
_REACHED_PHASE = "development"
_CONSUMED = 4321.0


def test_an_interrupt_checkpoints_the_phase_the_run_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """The saved checkpoint is the interrupted run's own state, not its first."""
    reached = PipelineState(
        phase=_REACHED_PHASE,
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=_CONSUMED,
    )

    def _advance_then_interrupt(
        _state: PipelineState,
        ctx: run_loop._LoopContext,
        _prev_phase: str,
    ) -> tuple[PipelineState, str, int | None]:
        ctx.latest_state[:] = [reached]
        raise KeyboardInterrupt

    saved: list[PipelineState] = []
    monkeypatch.setattr(run_loop, "_run_inner_loop", _advance_then_interrupt)
    monkeypatch.setattr(
        run_loop._runner_module,
        "save_checkpoint_or_log",
        lambda state, **_kwargs: saved.append(state),
    )

    exit_code = runner_module.run(
        MagicMock(),
        initial_state=PipelineState(phase="planning"),
        verbosity=Verbosity.QUIET,
    )

    assert exit_code == _INTERRUPT_EXIT_CODE
    assert [entry.phase for entry in saved] == [_REACHED_PHASE]
    assert saved[0].cycle_timebox_consumed_seconds == _CONSUMED
    assert saved[0].interrupted_by_user is True


def test_a_crash_reports_where_the_run_was_and_that_it_failed(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """An interrupt is not the only way out of the loop.

    Any other exception reached the report with the exit code still 0 and the
    state still the initial one, so a run that crashed hours into development
    was written up as `outcome: completed` in phase `planning` with no cycle
    section at all — a success report for a failure.
    """
    reached = PipelineState(
        phase=_REACHED_PHASE,
        cycle_timebox_active=True,
        cycle_timebox_consumed_seconds=_CONSUMED,
    )

    def _advance_then_crash(
        _state: PipelineState,
        ctx: run_loop._LoopContext,
        _prev_phase: str,
    ) -> tuple[PipelineState, str, int | None]:
        ctx.latest_state[:] = [reached]
        raise RuntimeError("transport died")

    reported: list[dict[str, object]] = []
    monkeypatch.setattr(run_loop, "_run_inner_loop", _advance_then_crash)
    monkeypatch.setattr(
        run_loop,
        "emit_run_time_report_safely",
        lambda _root, **kwargs: reported.append(kwargs),
    )

    with pytest.raises(RuntimeError):
        runner_module.run(
            MagicMock(),
            initial_state=PipelineState(phase="planning"),
            verbosity=Verbosity.QUIET,
        )

    assert len(reported) == 1
    assert reported[0]["state"].phase == _REACHED_PHASE
    assert reported[0]["outcome"] == "failed"

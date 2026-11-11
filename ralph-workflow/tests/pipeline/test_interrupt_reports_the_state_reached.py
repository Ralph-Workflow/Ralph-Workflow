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


pytestmark = pytest.mark.timeout_seconds(15)


def test_an_interrupt_checkpoints_the_phase_the_run_reached(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
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

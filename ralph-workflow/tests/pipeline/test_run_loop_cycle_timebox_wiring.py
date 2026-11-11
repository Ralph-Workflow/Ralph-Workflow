from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import MagicMock

from ralph.config.enums import Verbosity
from ralph.pipeline import run_loop
from ralph.pipeline import runner as runner_module
from ralph.pipeline.state import PipelineState

_EXPECTED_ARMING_CALLS = 2

if TYPE_CHECKING:
    import pytest


def _run_to_terminal(
    monkeypatch: pytest.MonkeyPatch,
    initial: PipelineState,
    on_step: object = None,
) -> None:

    def _step(**kwargs: object) -> PipelineState:
        if on_step is not None:
            on_step(kwargs)
        state = kwargs["state"]
        return state.copy_with(phase=kwargs["policy_bundle"].pipeline.terminal_phase)

    monkeypatch.setattr(run_loop._runner_module, "run_pipeline_step", _step)
    monkeypatch.setattr(
        run_loop, "_apply_startup_rebase_outcomes", lambda state, _ctx, **_kwargs: state
    )
    monkeypatch.setattr(run_loop, "_block_unresolved_integration", lambda *_args: None)
    runner_module.run(MagicMock(), initial_state=initial, verbosity=Verbosity.QUIET)


def test_the_loop_gives_each_step_a_monotonic_sample_box(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    boxes: list[object] = []

    _run_to_terminal(
        monkeypatch,
        PipelineState(phase="development"),
        on_step=lambda kwargs: boxes.append(kwargs.get("_cycle_sample_box")),
    )

    assert boxes
    assert all(box is not None for box in boxes)


def test_the_loop_arms_a_legacy_checkpoint_resumed_mid_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    armed: list[PipelineState] = []
    real_initializer = run_loop.initialize_legacy_cycle_on_resume

    def _spy(state: PipelineState, pipeline: object) -> PipelineState:
        armed.append(real_initializer(state, pipeline))
        return armed[-1]

    monkeypatch.setattr(run_loop, "initialize_legacy_cycle_on_resume", _spy)

    _run_to_terminal(monkeypatch, PipelineState(phase="development"))

    assert len(armed) == _EXPECTED_ARMING_CALLS
    assert armed[-1].cycle_timebox_active is True


def test_the_loop_threads_the_timebox_policy_into_the_report(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    reported: list[dict[str, object]] = []
    monkeypatch.setattr(
        run_loop,
        "emit_run_time_report_safely",
        lambda _root, **kwargs: reported.append(kwargs),
    )

    _run_to_terminal(monkeypatch, PipelineState(phase="development"))

    assert len(reported) == 1
    assert reported[0]["cycle_timebox"] is not None

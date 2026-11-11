from __future__ import annotations

import pytest

from ralph.pipeline.state import PipelineState


@pytest.fixture()
def _minimal_state() -> PipelineState:
    return PipelineState(
        phase="planning",
        budget_caps={"iteration": 3},
        outer_progress={"iteration": 1},
    )


def test_pipeline_state_has_no_iteration_scalar(_minimal_state: PipelineState) -> None:
    assert not hasattr(_minimal_state, "iteration"), (
        "PipelineState still has a scalar 'iteration' field; remove it and use "
        "state.get_outer_progress('iteration') instead."
    )


def test_pipeline_state_has_no_reviewer_pass_scalar() -> None:
    state = PipelineState(phase="planning")
    assert not hasattr(state, "reviewer_pass"), (
        "PipelineState still has a scalar 'reviewer_pass' field; remove it and use "
        "state.get_outer_progress('reviewer_pass') instead."
    )


def test_get_outer_progress_returns_correct_value(_minimal_state: PipelineState) -> None:
    assert _minimal_state.get_outer_progress("iteration") == 1


def test_get_outer_progress_returns_zero_for_unknown() -> None:
    state = PipelineState(phase="planning")
    assert state.get_outer_progress("iteration") == 0


def test_with_outer_progress_does_not_set_scalar_field() -> None:
    state = PipelineState(phase="planning")
    new_state = state.with_outer_progress("iteration", 3)
    assert new_state.get_outer_progress("iteration") == 3
    assert not hasattr(new_state, "iteration"), (
        "with_outer_progress still mirrors values into a scalar 'iteration' field."
    )

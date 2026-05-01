"""Contract test: PipelineState must have no scalar mirror fields for budget counters.

The scalar 'iteration' and 'reviewer_pass' fields are legacy artefacts of
the old hardcoded workflow. This test asserts they are absent from the model
and that only the generic dict-based API (get_outer_progress) is present.
"""

from __future__ import annotations

import pytest

from ralph.pipeline.state import PipelineState


@pytest.fixture()
def _minimal_state() -> PipelineState:
    return PipelineState(
        phase="planning",
        budget_remaining={"iteration": 2},
        outer_progress={"iteration": 1},
    )


def test_pipeline_state_has_no_iteration_scalar(_minimal_state: PipelineState) -> None:
    """PipelineState must not expose a scalar 'iteration' attribute."""
    assert not hasattr(_minimal_state, "iteration"), (
        "PipelineState still has a scalar 'iteration' field; remove it and use "
        "state.get_outer_progress('iteration') instead."
    )


def test_pipeline_state_has_no_reviewer_pass_scalar() -> None:
    """PipelineState must not expose a scalar 'reviewer_pass' attribute."""
    state = PipelineState(phase="planning")
    assert not hasattr(state, "reviewer_pass"), (
        "PipelineState still has a scalar 'reviewer_pass' field; remove it and use "
        "state.get_outer_progress('reviewer_pass') instead."
    )


def test_get_outer_progress_returns_correct_value(_minimal_state: PipelineState) -> None:
    """get_outer_progress reads from the generic outer_progress dict."""
    assert _minimal_state.get_outer_progress("iteration") == 1


def test_get_outer_progress_returns_zero_for_unknown() -> None:
    """get_outer_progress returns 0 for counters not yet set."""
    state = PipelineState(phase="planning")
    assert state.get_outer_progress("iteration") == 0


def test_with_outer_progress_does_not_set_scalar_field() -> None:
    """with_outer_progress must not attempt to copy a value to a scalar field."""
    state = PipelineState(phase="planning")
    new_state = state.with_outer_progress("iteration", 3)
    assert new_state.get_outer_progress("iteration") == 3  # noqa: PLR2004
    # Scalar field must not exist
    assert not hasattr(new_state, "iteration"), (
        "with_outer_progress still mirrors values into a scalar 'iteration' field."
    )

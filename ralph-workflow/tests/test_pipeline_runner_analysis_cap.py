"""Pipeline runner tests for analysis iteration cap behavior.

These tests verify correction-phase routing at the analysis cap.
"""

from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING, Any, cast

from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.loader import load_policy

if TYPE_CHECKING:
    from ralph.pipeline.effects import Effect
    from ralph.policy.models import PolicyBundle


# Path to the default policy directory
DEFAULT_POLICY_DIR = Path(__file__).parent.parent.parent / "ralph" / "policy" / "defaults"

# Analysis iteration cap value for testing
_DEV_MAX_ANALYSIS = 3


def _reduce(
    state: PipelineState,
    event: object,
    policy: PolicyBundle | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if policy is not None:
        return reducer_reduce(state, cast("Any", event), policy.pipeline)
    return reducer_reduce(state, cast("Any", event), None)


def _load_default_policy() -> PolicyBundle:
    """Load the default policy from the bundled defaults."""
    return load_policy(DEFAULT_POLICY_DIR)


class TestDevAnalysisCapTriggeredCorrectionRouting:
    """Test that analysis loopback at max still routes to development under the default policy."""

    def test_dev_analysis_at_max_routes_to_development(self) -> None:
        """At max-1 iterations, ANALYSIS_LOOPBACK still routes to development."""
        policy = _load_default_policy()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 2},  # max-1 where max=3
            loop_caps={"development_analysis_iteration": _DEV_MAX_ANALYSIS},
            budget_caps={"iteration": 3},
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.get_loop_iteration("development_analysis_iteration") == _DEV_MAX_ANALYSIS

    def test_dev_analysis_commit_resets_counter_and_increments_iteration(self) -> None:
        """COMMIT_SUCCESS after cap resets analysis_iteration and increments iteration."""
        policy = _load_default_policy()
        state = PipelineState(
            phase="development_commit",
            loop_iterations={"development_analysis_iteration": _DEV_MAX_ANALYSIS},
            outer_progress={"iteration": 1},
            budget_caps={"iteration": 3, "reviewer_pass": 2},
        )

        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        expected_iteration = state.get_outer_progress("iteration") + 1
        assert new_state.get_outer_progress("iteration") == expected_iteration
        assert new_state.get_loop_iteration("development_analysis_iteration") == 0



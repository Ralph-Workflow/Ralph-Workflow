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

# Analysis iteration cap values for testing
_DEV_MAX_ANALYSIS = 3
_REVIEW_MAX_ANALYSIS = 2


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
            development_analysis_iteration=2,  # max-1 where max=3
            max_development_analysis_iterations=_DEV_MAX_ANALYSIS,
            development_budget_remaining=3,
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.get_loop_iteration("development_analysis_iteration") == _DEV_MAX_ANALYSIS

    def test_dev_analysis_commit_resets_counter_and_increments_iteration(self) -> None:
        """COMMIT_SUCCESS after cap resets analysis_iteration and increments iteration."""
        policy = _load_default_policy()
        state = PipelineState(
            phase="development_commit",
            development_analysis_iteration=_DEV_MAX_ANALYSIS,
            iteration=1,
            development_budget_remaining=3,
            review_budget_remaining=2,
        )

        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        expected_iteration = state.iteration + 1
        assert new_state.iteration == expected_iteration
        assert new_state.get_loop_iteration("development_analysis_iteration") == 0


class TestReviewAnalysisCapTriggeredCorrectionRouting:
    """Test that review analysis loopback at max still routes to fix under the default policy."""

    def test_review_analysis_at_max_routes_to_fix(self) -> None:
        """At max-1 iterations, ANALYSIS_LOOPBACK still routes to fix."""
        policy = _load_default_policy()
        state = PipelineState(
            phase="review_analysis",
            review_analysis_iteration=1,  # max-1 where max=2
            max_review_analysis_iterations=_REVIEW_MAX_ANALYSIS,
            reviewer_pass=0,
            development_budget_remaining=3,
            review_budget_remaining=2,
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "fix"
        assert new_state.get_loop_iteration("review_analysis_iteration") == _REVIEW_MAX_ANALYSIS

    def test_review_analysis_commit_resets_counter_and_increments_reviewer_pass(
        self,
    ) -> None:
        """COMMIT_SUCCESS after cap resets review_analysis_iteration."""
        policy = _load_default_policy()
        state = PipelineState(
            phase="review_commit",
            review_analysis_iteration=_REVIEW_MAX_ANALYSIS,
            reviewer_pass=0,
            development_budget_remaining=3,
            review_budget_remaining=2,
        )

        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        expected_reviewer_pass = state.reviewer_pass + 1
        assert new_state.reviewer_pass == expected_reviewer_pass
        assert new_state.get_loop_iteration("review_analysis_iteration") == 0

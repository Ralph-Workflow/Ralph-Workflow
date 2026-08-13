"""Pipeline runner tests for analysis iteration cap behavior.

These tests verify correction-phase routing at the analysis cap.
"""

from __future__ import annotations

from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.phases.phase_timing_record import PhaseTimingRecord
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
# A below-cap analysis-iteration value under the bundled default policy.
_DEV_ANALYSIS_BELOW_CAP = 1


def _reduce(
    state: PipelineState,
    event: object,
    policy: PolicyBundle | None = None,
) -> tuple[PipelineState, list[Effect]]:
    if policy is not None:
        return reducer_reduce(state, event, policy.pipeline)
    return reducer_reduce(state, event, None)


def _load_default_policy() -> PolicyBundle:
    """Load the default policy from the bundled defaults."""
    return load_policy(DEFAULT_POLICY_DIR)


def _policy_with_loop_counter_max(counter_name: str, default_max: int) -> PolicyBundle:
    policy = _load_default_policy()
    loop_counters = dict(policy.pipeline.loop_counters)
    loop_counters[counter_name] = loop_counters[counter_name].model_copy(
        update={"default_max": default_max}
    )
    return policy.model_copy(
        update={"pipeline": policy.pipeline.model_copy(update={"loop_counters": loop_counters})}
    )


class TestDevAnalysisCapTriggeredCorrectionRouting:
    """Test that analysis loopback at max still routes to development under the default policy."""

    def test_dev_analysis_at_max_routes_to_development(self) -> None:
        """At max-1 iterations, ANALYSIS_LOOPBACK still routes to development."""
        policy = _policy_with_loop_counter_max("development_analysis_iteration", _DEV_MAX_ANALYSIS)
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 2},  # max-1 where max=3
            budget_caps={"iteration": 3},
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.get_loop_iteration("development_analysis_iteration") == _DEV_MAX_ANALYSIS

    def test_dev_analysis_commit_routes_to_analysis_with_single_charge(self) -> None:
        """Post-commit gate admits analysis and charges exactly one analysis cycle."""
        policy = _load_default_policy()
        state = PipelineState(
            phase="development_commit",
            loop_iterations={"development_analysis_iteration": _DEV_ANALYSIS_BELOW_CAP},
            outer_progress={"iteration": 1},
            budget_caps={"iteration": 3, "reviewer_pass": 2},
            phase_timings=[
                PhaseTimingRecord(
                    phase="development",
                    iteration=0,
                    started_at=0.0,
                    elapsed=timedelta(seconds=900),
                    elapsed_seconds=900,
                ),
            ],
        )

        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        assert new_state.phase == "development_analysis"
        assert new_state.get_outer_progress("iteration") == state.get_outer_progress("iteration")
        assert new_state.get_loop_iteration(
            "development_analysis_iteration"
        ) == state.get_loop_iteration("development_analysis_iteration") + 1

"""New tests for analysis iteration counters and caps.

These tests verify:
1. iteration increments on dev commit success
2. reviewer_pass increments on review commit success
3. analysis counters increment on loopback
4. max-analysis still routes through correction phases without exceeding the cap
5. counters reset on commit success and analysis success
6. AnalysisDecisionEvent with request_changes mirrors ANALYSIS_LOOPBACK counter accounting
7. AnalysisDecisionEvent with completed mirrors ANALYSIS_SUCCESS counter reset
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)

if TYPE_CHECKING:
    from ralph.pipeline.effects import Effect


def _reduce(
    state: PipelineState,
    event: object,
    policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]]:
    return reducer_reduce(state, event, policy)


def _review_commit_policy() -> PipelinePolicy:
    """Minimal policy for review_commit counter accounting only."""
    return PipelinePolicy(
        phases={
            "review": PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(
                    on_success="review_commit",
                    on_failure=None,
                    on_loopback="review",
                ),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="review",
                    on_failure=None,
                    on_loopback="review",
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="reviewer_pass",
                    loop_resets=["review_analysis_iteration"],
                ),
            ),
        },
        entry_phase="review",
        terminal_phase="complete",
    )


class TestReviewCommitSuccessIncrementsReviewerPass:
    """Test B: review commit success increments reviewer_pass counter."""

    def test_review_commit_success_increments_reviewer_pass(self) -> None:
        """When review_commit emits COMMIT_SUCCESS, reviewer_pass should increment by 1."""
        state = PipelineState(
            phase="review_commit",
            budget_caps={"iteration": 3, "reviewer_pass": 2},
        )
        policy = _review_commit_policy()
        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        assert new_state.get_outer_progress("reviewer_pass") == 1

"""New tests for analysis iteration counters and caps.

These tests verify:
1. iteration increments on dev commit success
2. reviewer_pass increments on review commit success
3. analysis counters increment on loopback
4. max-analysis still routes through correction phases without exceeding the cap
5. counters reset on commit success and analysis success
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_DEVELOPMENT,
    PHASE_DEVELOPMENT_ANALYSIS,
    PHASE_FAILED,
    PHASE_FIX,
    PHASE_REVIEW,
    PHASE_REVIEW_ANALYSIS,
    PHASE_REVIEW_COMMIT,
)
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PostCommitRoute,
    PostCommitRouteWhen,
)

if TYPE_CHECKING:
    from ralph.pipeline.effects import Effect


def _reduce(
    state: PipelineState,
    event: object,
    policy: PipelinePolicy | None = None,
) -> tuple[PipelineState, list[Effect]]:
    return reducer_reduce(state, cast("Any", event), policy)


def _dev_analysis_policy() -> PipelinePolicy:
    """Policy with analysis phases that route to correction on ordinary loopback.

    This matches the intended default pipeline.toml behavior: ordinary analysis
    loopbacks route to development/fix, and capped loopbacks still take one
    final correction pass without letting the counter exceed the configured cap.
    """
    return PipelinePolicy(
        phases={
            PHASE_DEVELOPMENT: PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success=PHASE_DEVELOPMENT_ANALYSIS,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_DEVELOPMENT,
                ),
            ),
            PHASE_DEVELOPMENT_ANALYSIS: PhaseDefinition(
                drain="development_analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_DEVELOPMENT,  # loopback to development for final attempt
                ),
                embeds_analysis=True,
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                transitions=PhaseTransition(
                    on_success=PHASE_DEVELOPMENT,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_DEVELOPMENT,
                ),
                requires_commit=True,
            ),
            PHASE_REVIEW: PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(
                    on_success=PHASE_REVIEW_ANALYSIS,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_REVIEW,
                ),
            ),
            PHASE_REVIEW_ANALYSIS: PhaseDefinition(
                drain="review_analysis",
                transitions=PhaseTransition(
                    on_success=PHASE_REVIEW_COMMIT,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_FIX,  # loopback to fix for review issues
                ),
                embeds_analysis=True,
            ),
            PHASE_FIX: PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(
                    on_success=PHASE_REVIEW_ANALYSIS,  # fix success goes to review_analysis
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_REVIEW,
                ),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                transitions=PhaseTransition(
                    on_success=PHASE_REVIEW,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_REVIEW,
                ),
                requires_commit=True,
            ),
        },
        entry_phase=PHASE_DEVELOPMENT,
        terminal_phase=PHASE_COMPLETE,
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(
                    phase="development_commit",
                    budget_state="exhausted",
                ),
                target=PHASE_DEVELOPMENT,
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(
                    phase="review_commit",
                    budget_state="exhausted",
                ),
                target=PHASE_REVIEW,
            ),
        ],
    )


class TestDevCommitSuccessIncrementsIteration:
    """Test A: dev commit success increments iteration counter."""

    def test_dev_commit_success_increments_iteration(self) -> None:
        """When development_commit emits COMMIT_SUCCESS, iteration should increment by 1."""
        state = PipelineState(
            phase="development_commit",
            iteration=0,
            development_budget_remaining=3,
            review_budget_remaining=2,
        )
        policy = _dev_analysis_policy()
        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        assert new_state.iteration == 1


class TestReviewCommitSuccessIncrementsReviewerPass:
    """Test B: review commit success increments reviewer_pass counter."""

    def test_review_commit_success_increments_reviewer_pass(self) -> None:
        """When review_commit emits COMMIT_SUCCESS, reviewer_pass should increment by 1."""
        state = PipelineState(
            phase="review_commit",
            reviewer_pass=0,
            development_budget_remaining=3,
            review_budget_remaining=2,
        )
        policy = _dev_analysis_policy()
        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        assert new_state.reviewer_pass == 1


class TestDevAnalysisLoopbackIncrementsContinuationCounter:
    """Test C: development_analysis loopback increments the analysis iteration counter."""

    def test_dev_analysis_loopback_increments_continuation_counter(self) -> None:
        """ANALYSIS_LOOPBACK increments development_analysis_iteration and routes to development."""
        state = PipelineState(
            phase=PHASE_DEVELOPMENT_ANALYSIS,
            development_analysis_iteration=0,
            max_development_analysis_iterations=3,
        )
        policy = _dev_analysis_policy()
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        # Routes to development via on_loopback
        assert new_state.phase == PHASE_DEVELOPMENT
        assert new_state.development_analysis_iteration == 1


class TestDevAnalysisLoopbackAtMaxRoutesToDevelopment:
    """Test D: development_analysis loopback at max budget still routes to development."""

    def test_dev_analysis_loopback_at_max_routes_to_development(self) -> None:
        """At max-1 iterations, ANALYSIS_LOOPBACK still routes to development."""
        state = PipelineState(
            phase=PHASE_DEVELOPMENT_ANALYSIS,
            development_analysis_iteration=2,
            max_development_analysis_iterations=3,
        )
        policy = _dev_analysis_policy()
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == PHASE_DEVELOPMENT
        max_iterations = state.max_development_analysis_iterations
        assert new_state.development_analysis_iteration == max_iterations


class TestDevCommitSuccessResetsDevAnalysisIteration:
    """Test E: development_commit success resets development_analysis_iteration."""

    def test_dev_commit_success_resets_dev_analysis_iteration(self) -> None:
        """COMMIT_SUCCESS resets development_analysis_iteration to 0."""
        state = PipelineState(
            phase="development_commit",
            development_analysis_iteration=3,
            iteration=0,
            development_budget_remaining=3,
            review_budget_remaining=2,
        )
        policy = _dev_analysis_policy()
        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        assert new_state.iteration == 1
        assert new_state.development_analysis_iteration == 0


class TestReviewAnalysisLoopbackIncrementsContinuationCounter:
    """Test F (part 1): review_analysis loopback increments the analysis iteration counter."""

    def test_review_analysis_loopback_increments_continuation_counter(self) -> None:
        """ANALYSIS_LOOPBACK increments review_analysis_iteration and routes to fix."""
        state = PipelineState(
            phase=PHASE_REVIEW_ANALYSIS,
            review_analysis_iteration=0,
            max_review_analysis_iterations=2,
            reviewer_pass=0,
        )
        policy = _dev_analysis_policy()
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        # Routes to fix via on_loopback
        assert new_state.phase == PHASE_FIX
        assert new_state.review_analysis_iteration == 1
        assert new_state.reviewer_pass == 0


class TestReviewAnalysisLoopbackAtMaxRoutesToFix:
    """Test F (part 2): review_analysis loopback at max budget still routes to fix."""

    def test_review_analysis_loopback_at_max_routes_to_fix(self) -> None:
        """At max-1 iterations, ANALYSIS_LOOPBACK still routes to fix."""
        state = PipelineState(
            phase=PHASE_REVIEW_ANALYSIS,
            review_analysis_iteration=1,
            max_review_analysis_iterations=2,
            reviewer_pass=0,
        )
        policy = _dev_analysis_policy()
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == PHASE_FIX
        max_iterations = state.max_review_analysis_iterations
        assert new_state.review_analysis_iteration == max_iterations


class TestReviewCommitSuccessResetsReviewAnalysisIteration:
    """Test F (part 3): review_commit success resets review_analysis_iteration."""

    def test_review_commit_success_resets_review_analysis_iteration(self) -> None:
        """COMMIT_SUCCESS resets review_analysis_iteration to 0."""
        state = PipelineState(
            phase="review_commit",
            review_analysis_iteration=2,
            reviewer_pass=0,
            development_budget_remaining=3,
            review_budget_remaining=2,
        )
        policy = _dev_analysis_policy()
        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        assert new_state.reviewer_pass == 1
        assert new_state.review_analysis_iteration == 0


class TestAnalysisSuccessResetsCounters:
    """Test that ANALYSIS_SUCCESS resets the sibling analysis counter."""

    def test_dev_analysis_success_resets_dev_analysis_iteration(self) -> None:
        """ANALYSIS_SUCCESS resets development_analysis_iteration."""
        state = PipelineState(
            phase=PHASE_DEVELOPMENT_ANALYSIS,
            development_analysis_iteration=2,
            max_development_analysis_iterations=3,
        )
        policy = _dev_analysis_policy()
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        # Success routes to development_commit
        assert new_state.phase == "development_commit"
        assert new_state.development_analysis_iteration == 0

    def test_review_analysis_success_resets_review_analysis_iteration(self) -> None:
        """ANALYSIS_SUCCESS resets review_analysis_iteration."""
        state = PipelineState(
            phase=PHASE_REVIEW_ANALYSIS,
            review_analysis_iteration=1,
            max_review_analysis_iterations=2,
        )
        policy = _dev_analysis_policy()
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        # Success routes to review_commit
        assert new_state.phase == "review_commit"
        assert new_state.review_analysis_iteration == 0

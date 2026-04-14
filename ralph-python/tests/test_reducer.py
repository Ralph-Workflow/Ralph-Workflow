"""Unit tests for the pure pipeline reducer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from ralph.config.enums import (
    PHASE_COMPLETE,
    PHASE_DEVELOPMENT,
    PHASE_FAILED,
    PHASE_FIX,
    PHASE_REVIEW,
    PHASE_REVIEW_COMMIT,
    PipelinePhase,
)
from ralph.pipeline.effects import ExitFailureEffect
from ralph.pipeline.events import PipelineEvent
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import AgentChainState, PipelineState
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


def _basic_pipeline_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            PHASE_DEVELOPMENT: PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success=PHASE_COMPLETE,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_DEVELOPMENT,
                ),
            ),
        },
        entry_phase=PHASE_DEVELOPMENT,
        terminal_phase=PHASE_COMPLETE,
    )


def _policy_with_transition(target_phase: PipelinePhase) -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "budget_transition": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success=target_phase,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_DEVELOPMENT,
                ),
            ),
            PHASE_DEVELOPMENT: PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success=PHASE_COMPLETE,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_DEVELOPMENT,
                ),
            ),
            PHASE_REVIEW: PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(
                    on_success=PHASE_COMPLETE,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_REVIEW,
                ),
            ),
        },
        entry_phase="budget_transition",
        terminal_phase=PHASE_COMPLETE,
    )


def _policy_with_post_commit_routes() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success=PHASE_DEVELOPMENT),
            ),
            PHASE_DEVELOPMENT: PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="development_analysis"),
            ),
            "development_analysis": PhaseDefinition(
                drain="development_analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback=PHASE_DEVELOPMENT,
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                transitions=PhaseTransition(on_success=PHASE_REVIEW),
            ),
            PHASE_REVIEW: PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(on_success="review_analysis", on_loopback=PHASE_FIX),
            ),
            "review_analysis": PhaseDefinition(
                drain="review_analysis",
                transitions=PhaseTransition(on_success="review_commit", on_loopback=PHASE_FIX),
            ),
            PHASE_FIX: PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(on_success=PHASE_REVIEW),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                transitions=PhaseTransition(on_success=PHASE_COMPLETE),
            ),
            PHASE_COMPLETE: PhaseDefinition(
                drain="complete",
                transitions=PhaseTransition(on_success=PHASE_COMPLETE, on_loopback=PHASE_COMPLETE),
            ),
        },
        entry_phase="planning",
        terminal_phase=PHASE_COMPLETE,
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="remaining"),
                target="planning",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="exhausted"),
                target=PHASE_REVIEW,
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="review_commit", budget_state="remaining"),
                target=PHASE_REVIEW,
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="review_commit", budget_state="exhausted"),
                target=PHASE_COMPLETE,
            ),
        ],
    )


def test_agent_success_advances_iteration() -> None:
    """Test that AGENT_SUCCESS in development advances iteration."""
    state = PipelineState(
        total_iterations=3,
        iteration=0,
        phase=PHASE_DEVELOPMENT,
    )
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS)
    assert new_state.iteration == 1
    assert new_state.phase == PHASE_DEVELOPMENT


def test_final_iteration_advances_to_review() -> None:
    """Test that AGENT_SUCCESS on final iteration advances to review."""
    state = PipelineState(
        total_iterations=2,
        iteration=1,
        phase=PHASE_DEVELOPMENT,
    )
    new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS)
    assert new_state.phase == PHASE_REVIEW


def test_review_clean_advances_to_commit() -> None:
    """Test that REVIEW_CLEAN advances to commit phase."""
    state = PipelineState(phase=PHASE_REVIEW)
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_CLEAN)
    assert new_state.phase == PHASE_REVIEW_COMMIT


def test_review_issues_found_advances_to_fix() -> None:
    """Test that REVIEW_ISSUES_FOUND advances to fix phase."""
    state = PipelineState(
        phase=PHASE_REVIEW,
        reviewer_pass=0,
        total_reviewer_passes=2,
    )
    new_state, _ = _reduce(state, PipelineEvent.REVIEW_ISSUES_FOUND)
    assert new_state.phase == PHASE_FIX
    assert new_state.reviewer_pass == 1


def test_fix_success_returns_to_review() -> None:
    """Test that FIX_SUCCESS returns to review phase."""
    state = PipelineState(
        phase=PHASE_FIX,
        reviewer_pass=0,
        total_reviewer_passes=2,
    )
    new_state, _ = _reduce(state, PipelineEvent.FIX_SUCCESS)
    assert new_state.phase == PHASE_REVIEW


def test_commit_success_advances_to_complete() -> None:
    """Test that COMMIT_SUCCESS advances to complete phase."""
    state = PipelineState(phase=PHASE_DEVELOPMENT)
    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS)
    assert new_state.phase == PHASE_COMPLETE


def test_policy_agent_success_unknown_phase_routes_to_failed() -> None:
    """Policy-driven agent success should fail when phase is missing from policy."""
    state = PipelineState(phase="missing")
    policy = _basic_pipeline_policy()

    new_state, effects = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)

    assert new_state.phase == PHASE_FAILED
    assert new_state.previous_phase == "missing"
    assert new_state.last_error == "Unknown phase: missing"
    assert effects == [ExitFailureEffect(reason="Unknown phase: missing")]


def test_phase_advance_event_ignores_unknown_policy_phase() -> None:
    """PHASE_ADVANCE should be a no-op when the phase is missing from the policy."""
    state = PipelineState(phase="missing")
    policy = _basic_pipeline_policy()

    new_state, effects = _reduce(state, PipelineEvent.PHASE_ADVANCE, policy)

    assert new_state == state
    assert effects == []


def test_fix_failure_policy_terminal_transition_emits_exit_failure() -> None:
    """FIX_FAILURE should fail the pipeline when the policy points to a terminal phase."""
    policy = PipelinePolicy(
        phases={
            PHASE_FIX: PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(
                    on_success=PHASE_COMPLETE,
                    on_failure=PHASE_FAILED,
                    on_loopback=PHASE_FIX,
                ),
            ),
        },
        entry_phase=PHASE_FIX,
        terminal_phase=PHASE_COMPLETE,
    )
    state = PipelineState(phase=PHASE_FIX)

    new_state, effects = _reduce(state, PipelineEvent.FIX_FAILURE, policy)

    assert new_state.phase == PHASE_FAILED
    assert new_state.previous_phase == PHASE_FIX
    assert effects == [ExitFailureEffect(reason="Fix phase failed")]


def test_phase_advance_decreases_development_budget() -> None:
    """Advancing to development should decrement the development budget."""
    policy = _policy_with_transition(PHASE_DEVELOPMENT)
    state = PipelineState(phase="budget_transition", development_budget_remaining=2)

    new_state, _ = _reduce(state, PipelineEvent.PHASE_ADVANCE, policy)

    assert new_state.phase == PHASE_DEVELOPMENT
    assert new_state.development_budget_remaining == 1


def test_phase_advance_clamps_review_budget_at_zero() -> None:
    """Review budget should never go negative when advancing to review."""
    policy = _policy_with_transition(PHASE_REVIEW)
    state = PipelineState(phase="budget_transition", review_budget_remaining=0)

    new_state, _ = _reduce(state, PipelineEvent.PHASE_ADVANCE, policy)

    assert new_state.phase == PHASE_REVIEW
    assert new_state.review_budget_remaining == 0


def test_commit_success_routes_development_commit_to_planning_when_budget_remaining() -> None:
    """COMMIT_SUCCESS should route development_commit to planning when budget remains."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="development_commit", development_budget_remaining=1)

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == "planning"
    assert new_state.previous_phase == "development_commit"


def test_commit_success_routes_development_commit_to_review_when_budget_exhausted() -> None:
    """COMMIT_SUCCESS should route development_commit to review when budget exhausted."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="development_commit", development_budget_remaining=0)

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == PHASE_REVIEW
    assert new_state.previous_phase == "development_commit"


def test_commit_success_routes_review_commit_to_review_when_budget_remaining() -> None:
    """COMMIT_SUCCESS should route review_commit to review when budget remains."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="review_commit", review_budget_remaining=1)

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == PHASE_REVIEW
    assert new_state.previous_phase == "review_commit"


def test_commit_success_routes_review_commit_to_complete_when_budget_exhausted() -> None:
    """COMMIT_SUCCESS should route review_commit to complete when budget exhausted."""
    policy = _policy_with_post_commit_routes()
    state = PipelineState(phase="review_commit", review_budget_remaining=0)

    new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)

    assert new_state.phase == PHASE_COMPLETE
    assert new_state.previous_phase == "review_commit"


def test_agent_failure_triggers_retry() -> None:
    """Test that AGENT_FAILURE increments retry count."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=0),
    )
    new_state, _ = _reduce(state, PipelineEvent.AGENT_FAILURE)
    assert new_state.dev_chain.retries == 1


def test_agent_failure_falls_back_to_next_agent() -> None:
    """Test that AGENT_FAILURE falls back to next agent after max retries."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude", "opencode"], current_index=0, retries=3),
    )
    new_state, _ = _reduce(state, PipelineEvent.AGENT_FAILURE)
    assert new_state.dev_chain.current_index == 1
    assert new_state.dev_chain.retries == 0


def test_agent_failure_with_exhausted_chain_fails() -> None:
    """Test that AGENT_FAILURE with exhausted chain transitions to failed."""
    state = PipelineState(
        phase=PHASE_DEVELOPMENT,
        dev_chain=AgentChainState(agents=["claude"], current_index=0, retries=3),
    )
    new_state, _ = _reduce(state, PipelineEvent.AGENT_FAILURE)
    assert new_state.phase == PHASE_FAILED


def test_checkpoint_saved_increments_count() -> None:
    """Test that CHECKPOINT_SAVED increments the checkpoint counter."""
    state = PipelineState(checkpoint_saved_count=0)
    new_state, _ = _reduce(state, PipelineEvent.CHECKPOINT_SAVED)
    assert new_state.checkpoint_saved_count == 1


def test_interrupted_sets_flag() -> None:
    """Test that INTERRUPTED sets the interrupted_by_user flag."""
    state = PipelineState(interrupted_by_user=False)
    new_state, _ = _reduce(state, PipelineEvent.INTERRUPTED)
    assert new_state.interrupted_by_user is True


def test_is_complete_returns_true_for_complete() -> None:
    """Test that is_complete() returns True for COMPLETE phase."""
    state = PipelineState(phase=PHASE_COMPLETE)
    assert state.is_complete() is True


def test_is_complete_returns_true_for_failed() -> None:
    """Test that is_complete() returns True for FAILED phase."""
    state = PipelineState(phase=PHASE_FAILED)
    assert state.is_complete() is True


def test_is_complete_returns_false_for_development() -> None:
    """Test that is_complete() returns False for DEVELOPMENT phase."""
    state = PipelineState(phase=PHASE_DEVELOPMENT)
    assert state.is_complete() is False


class TestAnalysisDecisionDispatch:
    """Tests for AnalysisDecision-driven routing through the reducer.

    These tests verify that ANALYSIS_SUCCESS and ANALYSIS_LOOPBACK events
    (emitted by the phase handlers based on AnalysisDecision values)
    correctly route between development_analysis/review_analysis and their
    target phases.
    """

    def test_analysis_success_routes_development_analysis_to_commit(self) -> None:
        """Test that ANALYSIS_SUCCESS in development_analysis routes to development_commit."""
        state = PipelineState(
            phase="development_analysis",
            iteration=0,
            total_iterations=2,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS)
        assert new_state.phase == "development_commit"
        assert new_state.previous_phase == "development_analysis"

    def test_analysis_loopback_routes_development_analysis_to_development(self) -> None:
        """Test that ANALYSIS_LOOPBACK in development_analysis routes back to development."""
        state = PipelineState(
            phase="development_analysis",
            iteration=0,
            total_iterations=2,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK)
        assert new_state.phase == PHASE_DEVELOPMENT
        assert new_state.previous_phase == "development_analysis"

    def test_analysis_success_routes_review_analysis_to_commit(self) -> None:
        """Test that ANALYSIS_SUCCESS in review_analysis routes to review_commit."""
        state = PipelineState(
            phase="review_analysis",
            reviewer_pass=0,
            total_reviewer_passes=2,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS)
        assert new_state.phase == "review_commit"
        assert new_state.previous_phase == "review_analysis"

    def test_analysis_loopback_routes_review_analysis_to_fix(self) -> None:
        """Test that ANALYSIS_LOOPBACK in review_analysis routes to fix."""
        state = PipelineState(
            phase="review_analysis",
            reviewer_pass=0,
            total_reviewer_passes=2,
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK)
        assert new_state.phase == PHASE_FIX
        assert new_state.previous_phase == "review_analysis"

    def test_analysis_success_with_policy_routes_correctly(self) -> None:
        """Test that ANALYSIS_SUCCESS respects pipeline policy routing."""
        # Build a minimal pipeline policy
        policy = PipelinePolicy(
            phases={
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development",
                    ),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="development_analysis",
                        on_loopback="development",
                    ),
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )

        state = PipelineState(phase="development_analysis")
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        assert new_state.phase == "development_commit"

    def test_analysis_loopback_with_policy_routes_correctly(self) -> None:
        """Test that ANALYSIS_LOOPBACK respects pipeline policy routing."""
        policy = PipelinePolicy(
            phases={
                "development_analysis": PhaseDefinition(
                    drain="development_analysis",
                    transitions=PhaseTransition(
                        on_success="development_commit",
                        on_loopback="development",
                    ),
                ),
                "development": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="development_analysis",
                        on_loopback="development",
                    ),
                ),
                "development_commit": PhaseDefinition(
                    drain="development_commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
                "complete": PhaseDefinition(
                    drain="development",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_loopback="complete",
                    ),
                ),
            },
            entry_phase="development_analysis",
            terminal_phase="complete",
        )

        state = PipelineState(phase="development_analysis")
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"

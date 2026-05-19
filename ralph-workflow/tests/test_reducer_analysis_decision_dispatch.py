"""Unit tests for the pure pipeline reducer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

from ralph.pipeline.events import (
    PipelineEvent,
)
from ralph.pipeline.progress import review_issues_found as _review_issues_found
from ralph.pipeline.reducer import reduce as reducer_reduce
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    BudgetCounterConfig,
    LoopCounterConfig,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
    PostCommitRoute,
    PostCommitRouteWhen,
    RecoveryPolicy,
)

DEVELOPMENT_ANALYSIS_TWO_RUN_CAP = 2

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
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
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="development",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
    )


def _policy_with_transition(target_phase: PipelinePhase) -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "budget_transition": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success=target_phase,
                    on_loopback="development",
                ),
            ),
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development",
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="review",
                ),
            ),
        },
        entry_phase="budget_transition",
        terminal_phase="complete",
    )


def _policy_with_post_commit_routes() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                transitions=PhaseTransition(on_success="development_analysis"),
            ),
            "development_analysis": PhaseDefinition(
                drain="development_analysis",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="development_analysis_iteration"),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="review",
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="iteration",
                    loop_resets=["development_analysis_iteration"],
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
                role="review",
                clean_outcome="clean",
                issues_outcome="has_issues",
                transitions=PhaseTransition(on_success="review_analysis", on_loopback="fix"),
                bypass_routes={"clean": "review_commit"},
            ),
            "review_analysis": PhaseDefinition(
                drain="review_analysis",
                role="analysis",
                transitions=PhaseTransition(on_success="review_commit", on_loopback="fix"),
                loop_policy=PhaseLoopPolicy(
                    iteration_state_field="review_analysis_iteration",
                    loopback_review_outcome="has_issues",
                ),
            ),
            "fix": PhaseDefinition(
                drain="fix",
                role="execution",
                transitions=PhaseTransition(
                    on_success="review_analysis",
                    on_loopback="review",
                ),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="complete",
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="reviewer_pass",
                    loop_resets=["review_analysis_iteration"],
                ),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        loop_counters={
            "development_analysis_iteration": LoopCounterConfig(default_max=3),
            "review_analysis_iteration": LoopCounterConfig(default_max=2),
        },
        budget_counters={
            "iteration": BudgetCounterConfig(default_max=5),
            "reviewer_pass": BudgetCounterConfig(default_max=1),
        },
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="remaining"),
                target="planning",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="exhausted"),
                target="review",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="no_review"),
                target="complete",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="review_commit", budget_state="remaining"),
                target="review",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="review_commit", budget_state="exhausted"),
                target="complete",
            ),
        ],
    )


def _policy_with_planning_analysis() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                role="execution",
                transitions=PhaseTransition(on_success="planning_analysis"),
            ),
            "planning_analysis": PhaseDefinition(
                drain="planning_analysis",
                role="analysis",
                transitions=PhaseTransition(on_success="development", on_loopback="planning"),
                loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
            ),
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                transitions=PhaseTransition(on_success="development_analysis"),
            ),
            "development_analysis": PhaseDefinition(
                drain="development_analysis",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="development_analysis_iteration"),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="review",
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="iteration",
                    loop_resets=["development_analysis_iteration"],
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
                role="review",
                clean_outcome="clean",
                issues_outcome="has_issues",
                transitions=PhaseTransition(on_success="review_analysis", on_loopback="fix"),
                bypass_routes={"clean": "review_commit"},
            ),
            "review_analysis": PhaseDefinition(
                drain="review_analysis",
                role="analysis",
                transitions=PhaseTransition(on_success="review_commit", on_loopback="fix"),
                loop_policy=PhaseLoopPolicy(
                    iteration_state_field="review_analysis_iteration",
                    loopback_review_outcome="has_issues",
                ),
            ),
            "fix": PhaseDefinition(
                drain="fix",
                role="execution",
                transitions=PhaseTransition(
                    on_success="review_analysis",
                    on_loopback="review",
                ),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="complete",
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="reviewer_pass",
                    loop_resets=["review_analysis_iteration"],
                ),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        loop_counters={
            "planning_analysis_iteration": LoopCounterConfig(default_max=10),
            "development_analysis_iteration": LoopCounterConfig(default_max=3),
            "review_analysis_iteration": LoopCounterConfig(default_max=2),
        },
        budget_counters={
            "iteration": BudgetCounterConfig(default_max=5),
            "reviewer_pass": BudgetCounterConfig(default_max=1),
        },
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="remaining"),
                target="planning",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="exhausted"),
                target="review",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="development_commit", budget_state="no_review"),
                target="complete",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="review_commit", budget_state="remaining"),
                target="review",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="review_commit", budget_state="exhausted"),
                target="complete",
            ),
        ],
    )


# =============================================================================
# PhaseFailureEvent tests
# =============================================================================


class TestAnalysisDecisionDispatch:
    """Tests for AnalysisDecision-driven routing through the reducer.

    These tests verify that ANALYSIS_SUCCESS and ANALYSIS_LOOPBACK events
    (emitted by the phase handlers based on AnalysisDecision values)
    correctly route between development_analysis/review_analysis and their
    target phases.
    """

    def test_analysis_success_routes_development_analysis_to_commit(self) -> None:
        """Test that ANALYSIS_SUCCESS in development_analysis routes to development_commit."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            budget_caps={"iteration": 2},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        assert new_state.phase == "development_commit"
        assert new_state.previous_phase == "development_analysis"

    def test_analysis_loopback_routes_development_analysis_to_development(self) -> None:
        """Test that ANALYSIS_LOOPBACK in development_analysis routes back to development."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            budget_caps={"iteration": 2},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.previous_phase == "development_analysis"

    def test_analysis_loopback_does_not_decrement_budget(self) -> None:
        """Analysis loopback re-enters development WITHOUT consuming a budget slot."""
        initial_budget = 2
        state = PipelineState(
            phase="development_analysis",
            budget_caps={"iteration": initial_budget},
        )
        policy = _policy_with_post_commit_routes()
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.get_budget_remaining("iteration") == initial_budget

    def test_analysis_success_routes_review_analysis_to_commit(self) -> None:
        """Test that ANALYSIS_SUCCESS in review_analysis routes to review_commit."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            budget_caps={"reviewer_pass": 2},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        assert new_state.phase == "review_commit"
        assert new_state.previous_phase == "review_analysis"

    def test_analysis_loopback_routes_review_analysis_to_fix(self) -> None:
        """Test that ANALYSIS_LOOPBACK in review_analysis routes to fix."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            budget_caps={"reviewer_pass": 2},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "fix"
        assert new_state.previous_phase == "review_analysis"

    def test_analysis_loopback_with_policy_marks_review_issue_without_completing_pass(self) -> None:
        """Policy routing must preserve review bookkeeping on loopback to fix."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            budget_caps={"reviewer_pass": 2},
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

        assert new_state.phase == "fix"
        assert new_state.get_outer_progress("reviewer_pass") == 0
        assert _review_issues_found(new_state, policy) is True

    def test_analysis_success_with_policy_clears_review_issue_flag(self) -> None:
        """Policy routing should clear stale review issue state on approval."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            outer_progress={"reviewer_pass": 1},
            budget_caps={"reviewer_pass": 2},
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)

        assert new_state.phase == "review_commit"
        assert _review_issues_found(new_state, policy) is False

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

    def test_dev_analysis_loopback_increments_dev_analysis_iteration(self) -> None:
        """ANALYSIS_LOOPBACK in development_analysis increments the iteration counter."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 0},
            loop_caps={"development_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.get_loop_iteration("development_analysis_iteration") == 1

    def test_dev_analysis_loopback_at_max_routes_to_development(self) -> None:
        """At max iterations, ANALYSIS_LOOPBACK still routes to development."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 2},
            loop_caps={"development_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.previous_phase == "development_analysis"
        assert new_state.get_loop_iteration(
            "development_analysis_iteration"
        ) == state.loop_caps.get("development_analysis_iteration", 3)

    def test_dev_analysis_loopback_already_at_cap_stays_clamped(self) -> None:
        """Further loopbacks after the cap should not increment beyond the cap."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 3},
            loop_caps={"development_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.previous_phase == "development_analysis"
        assert new_state.get_loop_iteration(
            "development_analysis_iteration"
        ) == state.loop_caps.get("development_analysis_iteration", 3)

    def test_dev_analysis_loopback_with_zero_cap_stays_zero(self) -> None:
        """A zero configured cap should still route to development without incrementing."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 0},
            loop_caps={"development_analysis_iteration": 0},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.previous_phase == "development_analysis"
        assert new_state.get_loop_iteration("development_analysis_iteration") == 0

    def test_dev_analysis_loopback_with_single_iteration_cap_clamps_to_one(self) -> None:
        """A cap of 1 should allow the first analysis run and clamp the loopback to 1."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 0},
            loop_caps={"development_analysis_iteration": 1},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.previous_phase == "development_analysis"
        assert new_state.get_loop_iteration("development_analysis_iteration") == 1

    def test_dev_analysis_loopback_with_two_iteration_cap_reaches_final_counter(self) -> None:
        """The second allowed analysis run should clamp to 2 so only the next re-entry skips."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 1},
            loop_caps={"development_analysis_iteration": DEVELOPMENT_ANALYSIS_TWO_RUN_CAP},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "development"
        assert new_state.previous_phase == "development_analysis"
        assert (
            new_state.get_loop_iteration("development_analysis_iteration")
            == DEVELOPMENT_ANALYSIS_TWO_RUN_CAP
        )

    def test_dev_analysis_success_resets_dev_analysis_iteration(self) -> None:
        """ANALYSIS_SUCCESS in development_analysis resets the iteration counter."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 2},
            loop_caps={"development_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        assert new_state.get_loop_iteration("development_analysis_iteration") == 0

    def test_commit_success_resets_dev_analysis_iteration(self) -> None:
        """COMMIT_SUCCESS in development_commit resets development_analysis_iteration."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="development_commit",
            loop_iterations={"development_analysis_iteration": 2},
            loop_caps={"development_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        assert new_state.get_loop_iteration("development_analysis_iteration") == 0

    def test_review_analysis_loopback_increments_review_analysis_iteration(self) -> None:
        """ANALYSIS_LOOPBACK in review_analysis increments the iteration counter."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 0},
            loop_caps={"review_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.get_loop_iteration("review_analysis_iteration") == 1

    def test_review_analysis_loopback_at_max_routes_to_fix(self) -> None:
        """At the review-analysis cap, ANALYSIS_LOOPBACK still routes to fix."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 2},
            loop_caps={"review_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "fix"
        assert new_state.previous_phase == "review_analysis"
        assert new_state.get_loop_iteration("review_analysis_iteration") == state.loop_caps.get(
            "review_analysis_iteration", 2
        )

    def test_review_analysis_loopback_already_at_cap_stays_clamped(self) -> None:
        """Further review loopbacks after the cap should not increment beyond the cap."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 3},
            loop_caps={"review_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "fix"
        assert new_state.previous_phase == "review_analysis"
        assert new_state.get_loop_iteration("review_analysis_iteration") == state.loop_caps.get(
            "review_analysis_iteration", 2
        )
        assert new_state.review_outcome is not None

    def test_review_analysis_loopback_with_zero_cap_stays_zero(self) -> None:
        """A zero configured cap should still route to fix without incrementing."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 0},
            loop_caps={"review_analysis_iteration": 0},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)
        assert new_state.phase == "fix"
        assert new_state.previous_phase == "review_analysis"
        assert new_state.get_loop_iteration("review_analysis_iteration") == 0
        assert new_state.review_outcome is not None

    def test_review_analysis_loopback_at_max_with_policy_routes_to_fix(self) -> None:
        """Policy routing must keep review analysis loopback on fix when the cap is reached."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 1},
            loop_caps={"review_analysis_iteration": 2},
        )

        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

        assert new_state.phase == "fix"
        assert new_state.previous_phase == "review_analysis"
        assert new_state.get_loop_iteration("review_analysis_iteration") == state.loop_caps.get(
            "review_analysis_iteration", 2
        )
        assert new_state.review_outcome is not None

    def test_review_analysis_success_resets_review_analysis_iteration(self) -> None:
        """ANALYSIS_SUCCESS in review_analysis resets the iteration counter."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 2},
            loop_caps={"review_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_SUCCESS, policy)
        assert new_state.get_loop_iteration("review_analysis_iteration") == 0

    def test_commit_success_resets_review_analysis_iteration(self) -> None:
        """COMMIT_SUCCESS in review_commit resets review_analysis_iteration."""
        policy = _policy_with_post_commit_routes()
        state = PipelineState(
            phase="review_commit",
            loop_iterations={"review_analysis_iteration": 2},
            loop_caps={"review_analysis_iteration": 3},
        )
        new_state, _ = _reduce(state, PipelineEvent.COMMIT_SUCCESS, policy)
        assert new_state.get_loop_iteration("review_analysis_iteration") == 0

    def test_dev_analysis_loopback_routing_error_preserves_iteration_bookkeeping(self) -> None:
        """Routing errors after capped dev loopback should keep the clamped counter."""
        policy = MagicMock()
        policy.recovery.failed_route = "failed"
        phase_def = MagicMock()
        phase_def.workflow_fallback = None
        phase_def.loop_policy = PhaseLoopPolicy(
            iteration_state_field="development_analysis_iteration"
        )
        phase_def.decisions = {}
        policy.phases.get.return_value = phase_def

        state = PipelineState(
            phase="development_analysis",
            loop_iterations={"development_analysis_iteration": 1},
            loop_caps={"development_analysis_iteration": 3},
        )
        with patch("ralph.pipeline.reducer.resolve_next_phase", side_effect=ValueError("bad")):
            new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

        assert new_state.phase == "failed"
        assert new_state.get_loop_iteration("development_analysis_iteration") == min(
            state.get_loop_iteration("development_analysis_iteration") + 1,
            state.loop_caps.get("development_analysis_iteration", 3),
        )

    def test_review_analysis_loopback_routing_error_preserves_iteration_bookkeeping(self) -> None:
        """Routing errors after capped review loopback should keep the clamped counter."""
        policy = MagicMock()
        policy.recovery.failed_route = "failed"
        phase_def = MagicMock()
        phase_def.workflow_fallback = None
        phase_def.loop_policy = PhaseLoopPolicy(
            iteration_state_field="review_analysis_iteration", loopback_review_outcome="has_issues"
        )
        phase_def.decisions = {}
        policy.phases.get.return_value = phase_def

        state = PipelineState(
            phase="review_analysis",
            loop_iterations={"review_analysis_iteration": 1},
            loop_caps={"review_analysis_iteration": 3},
        )
        with patch("ralph.pipeline.reducer.resolve_next_phase", side_effect=ValueError("bad")):
            new_state, _ = _reduce(state, PipelineEvent.ANALYSIS_LOOPBACK, policy)

        assert new_state.phase == "failed"
        assert new_state.get_loop_iteration("review_analysis_iteration") == min(
            state.get_loop_iteration("review_analysis_iteration") + 1,
            state.loop_caps.get("review_analysis_iteration", 2),
        )
        assert new_state.review_outcome is not None

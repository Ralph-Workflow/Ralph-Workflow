"""Unit tests for the pure pipeline reducer."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast
from unittest.mock import patch

from ralph.pipeline.events import (
    PipelineEvent,
)
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


def _with_loop_cap(policy: PipelinePolicy, field: str, cap: int) -> PipelinePolicy:
    return policy.model_copy(
        update={
            "loop_counters": {
                **policy.loop_counters,
                field: LoopCounterConfig(default_max=cap),
            }
        }
    )


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


class TestAnalysisBudgetBypass:
    """Tests for bypassing exhausted analysis phases on re-entry."""

    def test_planning_success_skips_exhausted_planning_analysis(self) -> None:
        policy = _policy_with_planning_analysis()
        policy = _with_loop_cap(policy, "planning_analysis_iteration", 10)
        state = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 10},
        )

        new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)

        assert new_state.phase == "development"
        assert new_state.previous_phase == "planning"
        assert new_state.get_loop_iteration("planning_analysis_iteration") == 0

    def test_planning_success_logs_effective_target_after_exhausted_analysis_bypass(self) -> None:
        policy = _policy_with_planning_analysis()
        policy = _with_loop_cap(policy, "planning_analysis_iteration", 10)
        state = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 10},
        )

        with patch(
            "ralph.pipeline.reducer.explain_routing_decision",
            return_value="routing",
        ) as explain:
            new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)

        assert new_state.phase == "development"
        assert explain.call_args is not None
        assert explain.call_args.args[1] == "development"

    def test_development_success_skips_exhausted_development_analysis(self) -> None:
        policy = _policy_with_planning_analysis()
        policy = _with_loop_cap(policy, "development_analysis_iteration", 3)
        state = PipelineState(
            phase="development",
            loop_iterations={"development_analysis_iteration": 3},
        )

        new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)

        assert new_state.phase == "development_commit"
        assert new_state.previous_phase == "development"
        assert new_state.get_loop_iteration("development_analysis_iteration") == 0

    def test_planning_success_skips_analysis_immediately_when_cap_is_zero(self) -> None:
        policy = _policy_with_planning_analysis()
        policy = _with_loop_cap(policy, "planning_analysis_iteration", 0)
        state = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 0},
        )

        new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)

        assert new_state.phase == "development"
        assert new_state.previous_phase == "planning"
        assert new_state.get_loop_iteration("planning_analysis_iteration") == 0

    def test_development_success_logs_effective_target_after_exhausted_analysis_bypass(
        self,
    ) -> None:
        policy = _policy_with_planning_analysis()
        policy = _with_loop_cap(policy, "development_analysis_iteration", 3)
        state = PipelineState(
            phase="development",
            loop_iterations={"development_analysis_iteration": 3},
        )

        with patch(
            "ralph.pipeline.reducer.explain_routing_decision",
            return_value="routing",
        ) as explain:
            new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)

        assert new_state.phase == "development_commit"
        assert explain.call_args is not None
        assert explain.call_args.args[1] == "development_commit"

    def test_fix_success_skips_exhausted_review_analysis(self) -> None:
        policy = _policy_with_planning_analysis()
        policy = _with_loop_cap(policy, "review_analysis_iteration", 2)
        state = PipelineState(
            phase="fix",
            loop_iterations={"review_analysis_iteration": 2},
            review_outcome="has_issues",
        )

        new_state, _ = _reduce(state, PipelineEvent.AGENT_SUCCESS, policy)

        assert new_state.phase == "review_commit"
        assert new_state.previous_phase == "fix"
        assert new_state.get_loop_iteration("review_analysis_iteration") == 0
        assert new_state.review_outcome is None

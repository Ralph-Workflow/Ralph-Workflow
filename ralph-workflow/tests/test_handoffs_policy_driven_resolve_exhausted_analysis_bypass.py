"""Tests that handoffs._compute_budget_state is policy-driven.

Verifies that budget routing works for any commit-role phase name, not just
the canonical development_commit/review_commit names, as long as commit_policy
declares the correct increments_counter.
"""

from __future__ import annotations

from ralph.pipeline.handoffs import resolve_exhausted_analysis_bypass
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
)


def _feature_commit_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "feature_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure=None,
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="iteration",
                    loop_resets=["development_analysis_iteration"],
                ),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="complete",
                ),
            ),
        },
        entry_phase="feature_commit",
        terminal_phase="complete",
        budget_counters={"iteration": BudgetCounterConfig(default_max=5)},
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="feature_commit", budget_state="remaining"),
                target="feature_commit",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="feature_commit", budget_state="exhausted"),
                target="complete",
            ),
        ],
    )


def _planning_bypass_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="planning_analysis"),
            ),
            "planning_analysis": PhaseDefinition(
                drain="planning_analysis",
                role="analysis",
                loop_policy=PhaseLoopPolicy(iteration_state_field="planning_analysis_iteration"),
                transitions=PhaseTransition(
                    on_success="development",
                    on_loopback="planning_analysis",
                ),
            ),
            "development": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(on_success="done"),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
        },
        entry_phase="planning",
        terminal_phase="done",
        loop_counters={"planning_analysis_iteration": LoopCounterConfig(default_max=3)},
    )


def _commit_cleanup_bypass_policy() -> PipelinePolicy:
    return PipelinePolicy(
        phases={
            "development": PhaseDefinition(
                drain="development",
                role="execution",
                transitions=PhaseTransition(on_success="development_commit_cleanup"),
            ),
            "development_commit_cleanup": PhaseDefinition(
                drain="commit",
                role="commit_cleanup",
                loop_policy=PhaseLoopPolicy(iteration_state_field="commit_cleanup_iteration"),
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development_commit_cleanup",
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(on_success="done"),
                commit_policy=PhaseCommitPolicy(loop_resets=["commit_cleanup_iteration"]),
            ),
            "done": PhaseDefinition(
                drain="done",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="done", on_loopback="done"),
            ),
        },
        entry_phase="development",
        terminal_phase="done",
        loop_counters={"commit_cleanup_iteration": LoopCounterConfig(default_max=3)},
    )


class TestResolveExhaustedAnalysisBypass:
    def test_bypass_resets_exhausted_analysis_and_routes_to_success_target(self) -> None:
        policy = _planning_bypass_policy()
        state = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 3},
            review_outcome="issues",
        )

        bypass = resolve_exhausted_analysis_bypass(state, "planning_analysis", policy)

        assert bypass.target_phase == "development"
        assert bypass.state.get_loop_iteration("planning_analysis_iteration") == 0
        assert bypass.state.review_outcome is None
        assert bypass.skipped[0].phase == "planning_analysis"
        assert bypass.skipped[0].target_phase == "development"

    def test_non_exhausted_analysis_target_is_left_unchanged(self) -> None:
        policy = _planning_bypass_policy()
        state = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 2},
        )

        bypass = resolve_exhausted_analysis_bypass(state, "planning_analysis", policy)

        assert bypass.target_phase == "planning_analysis"
        assert bypass.state == state
        assert bypass.skipped == ()

    def test_exhausted_bypass_is_consumed_exactly_once_on_second_call(self) -> None:
        policy = _planning_bypass_policy()
        state = PipelineState(
            phase="planning",
            loop_iterations={"planning_analysis_iteration": 3},
            review_outcome="issues",
        )

        first_bypass = resolve_exhausted_analysis_bypass(state, "planning_analysis", policy)

        assert first_bypass.target_phase == "development"
        assert first_bypass.state.get_loop_iteration("planning_analysis_iteration") == 0
        assert first_bypass.state.review_outcome is None
        assert len(first_bypass.skipped) == 1
        assert first_bypass.skipped[0].phase == "planning_analysis"
        assert first_bypass.skipped[0].target_phase == "development"

        second_bypass = resolve_exhausted_analysis_bypass(
            first_bypass.state,
            "planning_analysis",
            policy,
        )

        assert second_bypass.target_phase == "planning_analysis"
        assert second_bypass.state == first_bypass.state
        assert second_bypass.skipped == ()

    def test_commit_cleanup_candidate_uses_generic_loop_bypass(self) -> None:
        policy = _commit_cleanup_bypass_policy()
        state = PipelineState(
            phase="development",
            loop_iterations={"commit_cleanup_iteration": 3},
            review_outcome="issues",
        )

        bypass = resolve_exhausted_analysis_bypass(state, "development_commit_cleanup", policy)

        assert bypass.target_phase == "development_commit"
        assert bypass.state.get_loop_iteration("commit_cleanup_iteration") == 0
        assert len(bypass.skipped) == 1
        assert bypass.skipped[0].phase == "development_commit_cleanup"
        assert bypass.skipped[0].target_phase == "development_commit"

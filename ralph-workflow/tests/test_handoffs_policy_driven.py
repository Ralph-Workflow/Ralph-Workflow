"""Tests that handoffs._compute_budget_state is policy-driven.

Verifies that budget routing works for any commit-role phase name, not just
the canonical development_commit/review_commit names, as long as commit_policy
declares the correct increments_counter.
"""

from __future__ import annotations

from ralph.config.enums import PHASE_COMPLETE, PHASE_FAILED
from ralph.pipeline.handoffs import resolve_post_commit_phase
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    PhaseCommitPolicy,
    PhaseDefinition,
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
                    on_success=PHASE_COMPLETE,
                    on_failure=PHASE_FAILED,
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="iteration",
                    loop_resets=["development_analysis_iteration"],
                ),
            ),
            PHASE_COMPLETE: PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(
                    on_success=PHASE_COMPLETE,
                    on_loopback=PHASE_COMPLETE,
                ),
            ),
        },
        entry_phase="feature_commit",
        terminal_phase=PHASE_COMPLETE,
        post_commit_routes=[
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="feature_commit", budget_state="remaining"),
                target="feature_commit",
            ),
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="feature_commit", budget_state="exhausted"),
                target=PHASE_COMPLETE,
            ),
        ],
    )


class TestComputeBudgetStateUsingCommitPolicy:
    """_compute_budget_state reads commit_policy, not hardcoded phase names."""

    def test_custom_phase_with_budget_remaining_routes_correctly(self) -> None:
        policy = _feature_commit_policy()
        state = PipelineState(
            phase="feature_commit",
            development_budget_remaining=2,
            review_budget_remaining=1,
        )
        next_phase = resolve_post_commit_phase(state, policy)
        assert next_phase == "feature_commit"

    def test_custom_phase_with_budget_exhausted_routes_correctly(self) -> None:
        policy = _feature_commit_policy()
        state = PipelineState(
            phase="feature_commit",
            development_budget_remaining=0,
            review_budget_remaining=1,
        )
        next_phase = resolve_post_commit_phase(state, policy)
        assert next_phase == PHASE_COMPLETE

    def test_phase_without_commit_policy_falls_back_to_on_success(self) -> None:
        policy = PipelinePolicy(
            phases={
                "plain_commit": PhaseDefinition(
                    drain="development_commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success=PHASE_COMPLETE,
                        on_failure=PHASE_FAILED,
                    ),
                    # commit_policy absent
                ),
                PHASE_COMPLETE: PhaseDefinition(
                    drain="complete",
                    role="terminal",
                    terminal_outcome="success",
                    transitions=PhaseTransition(
                        on_success=PHASE_COMPLETE,
                        on_loopback=PHASE_COMPLETE,
                    ),
                ),
            },
            entry_phase="plain_commit",
            terminal_phase=PHASE_COMPLETE,
            post_commit_routes=[
                PostCommitRoute(
                    when=PostCommitRouteWhen(phase="plain_commit", budget_state="remaining"),
                    target="plain_commit",
                ),
            ],
        )
        state = PipelineState(
            phase="plain_commit",
            development_budget_remaining=5,
        )
        next_phase = resolve_post_commit_phase(state, policy)
        assert next_phase == PHASE_COMPLETE

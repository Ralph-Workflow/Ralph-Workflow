"""Unit tests for the orchestrator module.

Tests cover:
- determine_next_effect returns InvokeAgentEffect when agent not yet invoked
- analysis loopback routes back to development phase
- analysis success routes to development_commit
- development_commit with budget=0 routes to review
- unknown phase raises PhaseHandlerNotFoundError
"""

from __future__ import annotations

from ralph.pipeline.orchestrator import (
    resolve_post_commit_phase,
)
from ralph.pipeline.state import PipelineState
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    BudgetCounterConfig,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PostCommitRoute,
    PostCommitRouteWhen,
    RecoveryPolicy,
)


def _make_minimal_agents_policy() -> AgentsPolicy:
    """Create a minimal agents policy for testing."""
    return AgentsPolicy(
        agent_chains={
            "planning": AgentChainConfig(agents=["claude"], max_retries=2),
            "development": AgentChainConfig(agents=["claude", "opencode"], max_retries=3),
            "development_analysis": AgentChainConfig(agents=["claude"], max_retries=2),
            "development_commit": AgentChainConfig(agents=["claude"], max_retries=2),
            "review": AgentChainConfig(agents=["claude"], max_retries=3),
            "fix": AgentChainConfig(agents=["claude"], max_retries=3),
            "review_commit": AgentChainConfig(agents=["claude"], max_retries=2),
        },
        agent_drains={
            "planning": AgentDrainConfig(chain="planning"),
            "development": AgentDrainConfig(chain="development"),
            "development_analysis": AgentDrainConfig(chain="development_analysis"),
            "development_commit": AgentDrainConfig(chain="development_commit"),
            "review": AgentDrainConfig(chain="review"),
            "fix": AgentDrainConfig(chain="fix"),
            "review_commit": AgentDrainConfig(chain="review_commit"),
        },
    )


def _make_minimal_pipeline_policy() -> PipelinePolicy:
    """Create a minimal pipeline policy for testing."""
    return PipelinePolicy(
        phases={
            "planning": PhaseDefinition(
                drain="planning",
                transitions=PhaseTransition(on_success="development"),
            ),
            "development": PhaseDefinition(
                drain="development",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="development_commit",
                    on_loopback="development",
                ),
            ),
            "development_commit": PhaseDefinition(
                drain="development_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="review",
                    on_failure="failed_terminal",
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="iteration",
                    loop_resets=["development_analysis_iteration"],
                ),
            ),
            "review": PhaseDefinition(
                drain="review",
                role="analysis",
                transitions=PhaseTransition(
                    on_success="review_commit",
                    on_loopback="fix",
                ),
            ),
            "fix": PhaseDefinition(
                drain="fix",
                transitions=PhaseTransition(on_success="review"),
            ),
            "review_commit": PhaseDefinition(
                drain="review_commit",
                role="commit",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_failure="failed_terminal",
                ),
                commit_policy=PhaseCommitPolicy(
                    increments_counter="reviewer_pass",
                    loop_resets=["review_analysis_iteration"],
                ),
            ),
            "complete": PhaseDefinition(
                drain="development",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="complete",
                ),
            ),
            "failed_terminal": PhaseDefinition(
                drain="development",
                role="terminal",
                terminal_outcome="failure",
                transitions=PhaseTransition(on_success="failed_terminal"),
            ),
        },
        entry_phase="planning",
        terminal_phase="complete",
        recovery=RecoveryPolicy(failed_route="failed_terminal"),
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


def _make_state(phase: str = "planning", **overrides: object) -> PipelineState:
    """Create a pipeline state for testing."""
    return PipelineState(phase=phase, **overrides)


class TestCommitBudgetRouting:
    """Tests for commit-gated phase routing based on budget."""

    def test_development_commit_with_budget_remaining_routes_to_planning(self) -> None:
        """Post-commit route should send development_commit to planning when budget remains."""
        state = PipelineState(phase="development_commit", budget_caps={"iteration": 1})
        next_phase = resolve_post_commit_phase(state, _make_minimal_pipeline_policy())
        assert next_phase == "planning"

    def test_development_commit_with_budget_exhausted_routes_to_review(self) -> None:
        """Post-commit route should send development_commit to review when budget exhausted."""
        state = PipelineState(
            phase="development_commit",
            budget_caps={"iteration": 1, "reviewer_pass": 1},
            outer_progress={"iteration": 1},
        )
        next_phase = resolve_post_commit_phase(state, _make_minimal_pipeline_policy())
        assert next_phase == "review"

    def test_review_commit_with_budget_remaining_routes_to_review(self) -> None:
        """Post-commit route should send review_commit to review when budget remains."""
        state = PipelineState(phase="review_commit", budget_caps={"reviewer_pass": 1})
        next_phase = resolve_post_commit_phase(state, _make_minimal_pipeline_policy())
        assert next_phase == "review"

    def test_review_commit_on_success_routes_to_complete(self) -> None:
        """Test that review_commit with exhausted budget routes to complete."""
        state = PipelineState(
            phase="review_commit",
            budget_caps={"reviewer_pass": 1},
            outer_progress={"reviewer_pass": 1},
        )
        next_phase = resolve_post_commit_phase(state, _make_minimal_pipeline_policy())
        assert next_phase == "complete"

    def test_dev_commit_exhausted_no_review_routes_to_complete(self) -> None:
        """Post-commit route sends development_commit with exhausted budgets to complete."""
        state = PipelineState(
            phase="development_commit",
            budget_caps={"iteration": 1, "reviewer_pass": 1},
            outer_progress={"iteration": 1, "reviewer_pass": 1},
        )
        next_phase = resolve_post_commit_phase(state, _make_minimal_pipeline_policy())
        assert next_phase == "complete"

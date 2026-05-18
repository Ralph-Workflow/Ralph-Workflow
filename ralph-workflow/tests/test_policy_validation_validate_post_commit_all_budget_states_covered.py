"""Unit tests for policy validation.

Tests cover:
- Valid agents.toml loading
- Missing drain binding raises PolicyValidationError
- Chain referencing unknown agent raises PolicyValidationError
- Empty chain list raises PolicyValidationError
- All six built-in drains are bound in default agents.toml
"""

from __future__ import annotations

import importlib
from typing import cast

import pytest

from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
    ArtifactsPolicy,
    BudgetCounterConfig,
    DrainName,
    PhaseCommitPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    PostCommitRoute,
    PostCommitRouteWhen,
    RecoveryPolicy,
)
from ralph.policy.validation import (
    PolicyValidationError,
    validate_policy_completeness,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestValidatePostCommitAllBudgetStatesCovered:
    """Tests for _validate_post_commit_routes_complete requiring all three budget states.

    When a commit phase increments a tracked budget counter, post_commit_routes must
    cover all three budget states (remaining, exhausted, no_review) so the runtime
    always has an unambiguous route after commit.
    """

    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = cast(
            "dict[DrainName, AgentDrainConfig]",
            {d: AgentDrainConfig(chain=d) for d in drains},
        )
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_success(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def _terminal_failure(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="crashed",
            role="terminal",
            terminal_outcome="failure",
            transitions=PhaseTransition(on_success="crashed", on_loopback="crashed"),
        )

    def _bundle_with_routes(self, routes: list[tuple[str, str]]) -> PolicyBundle:
        agents = self._agents(["work", "commit", "complete", "crashed"])
        post_commit = [
            PostCommitRoute(
                when=PostCommitRouteWhen(phase="commit", budget_state=state),
                target=target,
            )
            for state, target in routes
        ]
        pipeline = PipelinePolicy(
            phases={
                "work": PhaseDefinition(
                    drain="work",
                    role="execution",
                    transitions=PhaseTransition(on_success="commit"),
                ),
                "commit": PhaseDefinition(
                    drain="commit",
                    role="commit",
                    transitions=PhaseTransition(
                        on_success="complete",
                        on_failure="crashed",
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_success(),
                "crashed": self._terminal_failure(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=True, default_max=5)},
            post_commit_routes=post_commit,
            recovery=RecoveryPolicy(failed_route="crashed"),
        )
        return PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )

    def test_missing_remaining_state_raises(self) -> None:
        """Only exhausted+no_review present: remaining missing → validation fails."""
        bundle = self._bundle_with_routes(
            [
                ("exhausted", "complete"),
                ("no_review", "complete"),
            ]
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert "remaining" in str(exc_info.value)

    def test_missing_exhausted_state_raises(self) -> None:
        """Only remaining+no_review present: exhausted missing → validation fails."""
        bundle = self._bundle_with_routes(
            [
                ("remaining", "work"),
                ("no_review", "complete"),
            ]
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert "exhausted" in str(exc_info.value)

    def test_missing_no_review_state_raises(self) -> None:
        """Only remaining+exhausted present: no_review missing → validation fails."""
        bundle = self._bundle_with_routes(
            [
                ("remaining", "work"),
                ("exhausted", "complete"),
            ]
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        assert "no_review" in str(exc_info.value)

    def test_all_three_budget_states_passes(self) -> None:
        """All three budget states declared: validation passes."""
        bundle = self._bundle_with_routes(
            [
                ("remaining", "work"),
                ("exhausted", "complete"),
                ("no_review", "complete"),
            ]
        )
        validate_policy_completeness(bundle)  # must not raise

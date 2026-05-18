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


class TestValidatePostCommitRoutesCoverage:
    """Tests for post_commit_routes coverage validation.

    A commit-role phase that increments a tracked budget counter must have at
    least one [[post_commit_routes]] entry. Missing routes allow silent
    fall-through on on_success, which is false configurability.
    """

    def _agents(self, drains: list[str]) -> AgentsPolicy:
        chains = {d: AgentChainConfig(agents=["claude"]) for d in drains}
        agent_drains = cast(
            "dict[DrainName, AgentDrainConfig]",
            {d: AgentDrainConfig(chain=d) for d in drains},
        )
        return AgentsPolicy(agent_chains=chains, agent_drains=agent_drains)

    def _terminal_phase(self) -> PhaseDefinition:
        return PhaseDefinition(
            drain="complete",
            role="terminal",
            terminal_outcome="success",
            transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
        )

    def test_post_commit_routes_required_for_tracked_counter(self) -> None:
        """Commit phase with tracked budget counter and no matching post_commit_routes fails."""
        agents = self._agents(["work", "commit", "complete"])
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
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=True, default_max=5)},
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError) as exc_info:
            validate_policy_completeness(bundle)
        error_msg = str(exc_info.value)
        assert "no post_commit_routes apply to this phase" in error_msg
        assert "commit" in error_msg

    def test_post_commit_routes_present_for_tracked_counter_passes(self) -> None:
        """Commit phase with tracked counter AND all three budget states declared passes."""
        agents = self._agents(["work", "commit", "complete"])
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
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=True, default_max=5)},
            post_commit_routes=[
                PostCommitRoute(
                    when=PostCommitRouteWhen(phase="commit", budget_state="remaining"),
                    target="work",
                ),
                PostCommitRoute(
                    when=PostCommitRouteWhen(phase="commit", budget_state="exhausted"),
                    target="complete",
                ),
                PostCommitRoute(
                    when=PostCommitRouteWhen(phase="commit", budget_state="no_review"),
                    target="complete",
                ),
            ],
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise

    def test_untracked_counter_does_not_require_post_commit_routes(self) -> None:
        """Commit phase with untracked (tracks_budget=False) counter needs no routes."""
        agents = self._agents(["work", "commit", "complete"])
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
                        on_failure=None,
                    ),
                    commit_policy=PhaseCommitPolicy(
                        increments_counter="cycles",
                        loop_resets=[],
                    ),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="work",
            terminal_phase="complete",
            budget_counters={"cycles": BudgetCounterConfig(tracks_budget=False, default_max=0)},
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise

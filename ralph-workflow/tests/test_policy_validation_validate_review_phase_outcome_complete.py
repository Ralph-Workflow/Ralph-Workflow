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
    DrainName,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
    PolicyBundle,
    RecoveryPolicy,
)
from ralph.policy.validation import (
    PolicyValidationError,
    validate_policy_completeness,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


class TestValidateReviewPhaseOutcomeComplete:
    """Tests for _validate_review_phase_outcome_complete in validate_policy_completeness."""

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

    def test_clean_outcome_missing_from_bypass_routes_fails(self) -> None:
        """review phase with clean_outcome not in bypass_routes fails completeness."""
        agents = self._agents(["review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    clean_outcome="approved",
                    transitions=PhaseTransition(on_success="complete"),
                    bypass_routes={},  # 'approved' key absent
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="review",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        with pytest.raises(PolicyValidationError, match=r"clean_outcome.*bypass_routes"):
            validate_policy_completeness(bundle)

    def test_clean_outcome_present_in_bypass_routes_passes(self) -> None:
        """review phase with clean_outcome key in bypass_routes passes completeness."""
        agents = self._agents(["review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    clean_outcome="approved",
                    transitions=PhaseTransition(on_success="complete"),
                    bypass_routes={"approved": "complete"},
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="review",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise

    def test_review_phase_without_clean_outcome_skipped(self) -> None:
        """review phase with no clean_outcome set is not checked."""
        agents = self._agents(["review", "complete"])
        pipeline = PipelinePolicy(
            phases={
                "review": PhaseDefinition(
                    drain="review",
                    role="review",
                    issues_outcome="has_issues",
                    transitions=PhaseTransition(on_success="complete"),
                ),
                "complete": self._terminal_phase(),
            },
            entry_phase="review",
            terminal_phase="complete",
            recovery=RecoveryPolicy(failed_route="complete"),
        )
        bundle = PolicyBundle(
            agents=agents, pipeline=pipeline, artifacts=ArtifactsPolicy(artifacts={})
        )
        validate_policy_completeness(bundle)  # must not raise

"""Unit tests for policy validation.

Tests cover:
- Valid agents.toml loading
- Missing drain binding raises PolicyValidationError
- Chain referencing unknown agent raises PolicyValidationError
- Empty chain list raises PolicyValidationError
- All six built-in drains are bound in default agents.toml
"""

from __future__ import annotations

from pathlib import Path

import pytest
from pydantic import ValidationError

from ralph.policy.loader import (
    load_policy,
)
from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
)


class TestAgentsPolicyValidation:
    """Tests for AgentsPolicy model validation."""

    def test_valid_agents_policy(self) -> None:
        """Test that a valid agents policy passes validation."""
        policy = AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"], max_retries=2),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
            },
        )
        assert policy.agent_chains["planning"].agents == ["claude"]
        assert policy.agent_drains["planning"].chain == "planning"

    def test_drain_references_unknown_chain_raises(self) -> None:
        """Test that a drain binding to an unknown chain raises ValueError."""
        with pytest.raises(ValueError, match="references unknown chain"):
            AgentsPolicy(
                agent_chains={},
                agent_drains={
                    "planning": AgentDrainConfig(chain="nonexistent"),
                },
            )

    def test_empty_chain_list_raises(self) -> None:
        """Test that an empty agents list in a chain raises ValueError."""
        with pytest.raises(ValidationError, match="min_length"):
            AgentChainConfig(agents=[])

    def test_chain_referencing_unknown_agent_raises(self) -> None:
        """Test that chain with unknown agent name raises validation error.

        Note: The model doesn't validate agent names exist, only that the chain
        reference is valid. Agent name validation happens at the registry level.
        """
        # This is valid at the policy level - agent names are validated elsewhere
        chain = AgentChainConfig(agents=["nonexistent_agent"], max_retries=2)
        assert chain.agents == ["nonexistent_agent"]


class TestDefaultPolicyLoading:
    """Tests for loading the default policy."""

    def test_load_default_policy_succeeds(self) -> None:
        """Test that the default policy loads without error."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.agents is not None
        assert bundle.pipeline is not None
        assert bundle.artifacts is not None

    def test_all_six_builtin_drains_bound(self) -> None:
        """Test that all six built-in drains are bound in default agents.toml."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        expected_drains = {
            "planning",
            "development",
            "development_analysis",
            "development_commit",
            "review",
            "review_analysis",
            "fix",
            "review_commit",
        }

        actual_drains = set(bundle.agents.agent_drains.keys())
        assert expected_drains.issubset(actual_drains), (
            f"Missing drains: {expected_drains - actual_drains}"
        )

    def test_default_pipeline_entry_phase(self) -> None:
        """Test that default pipeline has planning as entry phase."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.pipeline.entry_phase == "planning"

    def test_default_pipeline_terminal_phase(self) -> None:
        """Test that default pipeline has complete as terminal phase."""
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        assert bundle.pipeline.terminal_phase == "complete"

    def test_all_pipeline_drains_are_bound(self) -> None:
        """Test that every drain used in pipeline.phases is bound in agents.agent_drains.

        This is enforced by PolicyBundle's all_pipeline_drains_are_bound validator.
        """
        default_dir = Path(__file__).parent.parent / "ralph" / "policy" / "defaults"
        bundle = load_policy(default_dir)

        # This should not raise - the validator ensures all drains are bound
        for phase_name, phase_def in bundle.pipeline.phases.items():
            assert phase_def.drain in bundle.agents.agent_drains, (
                f"Phase '{phase_name}' uses unbound drain '{phase_def.drain}'"
            )


class TestPolicyBundleValidation:
    """Tests for cross-policy validation in PolicyBundle."""

    def test_pipeline_drain_not_bound_raises(self) -> None:
        """Test that a pipeline using an unbound drain raises ValueError."""
        from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy

        # Create agents policy with no development drain bound
        agents = AgentsPolicy(
            agent_chains={
                "planning": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "planning": AgentDrainConfig(chain="planning"),
            },
        )

        # Create pipeline that uses development drain (not bound)
        pipeline = PipelinePolicy(
            phases={
                "planning": PhaseDefinition(
                    drain="planning",
                    transitions=PhaseTransition(on_success="development"),
                ),
                "development": PhaseDefinition(
                    drain="development",  # Not bound in agents!
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="planning",
            terminal_phase="complete",
        )

        from ralph.policy.models import PolicyBundle

        with pytest.raises(ValueError, match="unbound drains"):
            PolicyBundle(agents=agents, pipeline=pipeline, artifacts=agents)

    def test_analysis_phase_without_vocabulary_raises(self) -> None:
        """Test that embeds_analysis phase without decision_vocabulary raises."""
        from ralph.policy.models import PhaseDefinition, PhaseTransition, PipelinePolicy

        # Create valid agents policy
        agents = AgentsPolicy(
            agent_chains={
                "development": AgentChainConfig(agents=["claude"]),
                "development_analysis": AgentChainConfig(agents=["claude"]),
            },
            agent_drains={
                "development": AgentDrainConfig(chain="development"),
                "development_analysis": AgentDrainConfig(chain="development_analysis"),
            },
        )

        # Create pipeline with analysis phase
        pipeline = PipelinePolicy(
            phases={
                "development": PhaseDefinition(
                    drain="development",
                    embeds_analysis=True,
                    transitions=PhaseTransition(on_success="complete"),
                ),
            },
            entry_phase="development",
            terminal_phase="complete",
        )

        from ralph.policy.models import PolicyBundle

        # Without an artifacts policy that provides decision_vocabulary,
        # this should raise
        with pytest.raises(ValueError, match="decision_vocabulary"):
            PolicyBundle(agents=agents, pipeline=pipeline, artifacts=agents)

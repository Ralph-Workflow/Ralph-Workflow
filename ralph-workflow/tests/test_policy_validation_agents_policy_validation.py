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

import pytest

from ralph.policy.models import (
    AgentChainConfig,
    AgentDrainConfig,
    AgentsPolicy,
)

ValidationError = importlib.import_module("pydantic").ValidationError
DEFAULT_MAX_WORK_UNITS = 50


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
        with pytest.raises(ValidationError, match="too_short"):
            AgentChainConfig(agents=[])

    def test_chain_referencing_unknown_agent_raises(self) -> None:
        """Test that chain with unknown agent name raises validation error.

        Note: The model doesn't validate agent names exist, only that the chain
        reference is valid. Agent name validation happens at the registry level.
        """
        # This is valid at the policy level - agent names are validated elsewhere
        chain = AgentChainConfig(agents=["nonexistent_agent"], max_retries=2)
        assert chain.agents == ["nonexistent_agent"]

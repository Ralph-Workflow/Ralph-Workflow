"""Tests for ralph/mcp/capability_mapping.py — MCP capability mapping layer."""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.mcp.protocol.capability_mapping import (
    PolicyMode,
    drain_to_policy_mode,
)
from ralph.policy.loader import load_agents_policy
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.policy.validation import PolicyValidationError


def _builtin_agents_policy() -> AgentsPolicy:
    return load_agents_policy(Path("/nonexistent"))


# =============================================================================
# Helper function tests
# =============================================================================


class TestDrainToPolicyMode:
    def test_development(self) -> None:
        assert (
            drain_to_policy_mode("development", _builtin_agents_policy()) == PolicyMode.DEVELOPMENT
        )

    def test_fix(self) -> None:
        assert drain_to_policy_mode("fix", _builtin_agents_policy()) == PolicyMode.FIX

    def test_unknown_raises_policy_validation_error(self) -> None:
        with pytest.raises(PolicyValidationError):
            drain_to_policy_mode("unknown")

    def test_custom_drain_with_explicit_drain_class(self) -> None:
        policy = AgentsPolicy(
            agent_chains={"c": AgentChainConfig(agents=["agent"])},
            agent_drains={"my_custom_audit": AgentDrainConfig(chain="c", drain_class="analysis")},
        )
        assert drain_to_policy_mode("my_custom_audit", policy) == PolicyMode.ANALYSIS

    def test_custom_commit_drain_without_policy_raises(self) -> None:
        with pytest.raises(PolicyValidationError):
            drain_to_policy_mode("feature_commit")

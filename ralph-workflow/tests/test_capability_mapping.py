"""Tests for drain identity handling in capability mapping."""

from __future__ import annotations

import pytest

from ralph.mcp.protocol.capability_mapping import (
    Capability,
    DrainClass,
    McpCapability,
    PolicyMode,
    SessionDrain,
    drain_class_for_session,
    drain_to_policy_mode,
    lookup_ralph_capability,
)
from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.policy.validation import PolicyValidationError


def test_drain_class_requires_policy_for_canonical_drains() -> None:
    """Canonical drains must be resolved from declared policy, not hidden fallback."""

    with pytest.raises(PolicyValidationError):
        drain_class_for_session(SessionDrain.PLANNING)


def test_drain_class_preserves_analysis_identity() -> None:
    """Analysis must not collapse to planning drain class."""

    policy = AgentsPolicy(
        agent_chains={"default": AgentChainConfig(agents=["agent"])},
        agent_drains={
            "planning": AgentDrainConfig(chain="default", drain_class="planning"),
            "analysis": AgentDrainConfig(chain="default", drain_class="analysis"),
        },
    )

    assert drain_class_for_session(SessionDrain.PLANNING, policy) is DrainClass.PLANNING
    assert drain_class_for_session(SessionDrain.ANALYSIS, policy) is DrainClass.ANALYSIS


def test_drain_policy_mode_preserves_read_only_drain_identity() -> None:
    """Read-only drains keep distinct policy identities."""

    policy = AgentsPolicy(
        agent_chains={"default": AgentChainConfig(agents=["agent"])},
        agent_drains={
            "planning": AgentDrainConfig(chain="default", drain_class="planning"),
            "analysis": AgentDrainConfig(chain="default", drain_class="analysis"),
            "review": AgentDrainConfig(chain="default", drain_class="review"),
        },
    )

    assert drain_to_policy_mode(SessionDrain.PLANNING, policy) is PolicyMode.PLANNING
    assert drain_to_policy_mode(SessionDrain.ANALYSIS, policy) is PolicyMode.ANALYSIS
    assert drain_to_policy_mode(SessionDrain.REVIEW, policy) is PolicyMode.REVIEW


@pytest.mark.parametrize("unresolvable_alias", ["dev", "read_only"])
def test_lossy_role_aliases_are_rejected(unresolvable_alias: str) -> None:
    """Aliases with no substring match raise PolicyValidationError."""

    with pytest.raises(PolicyValidationError):
        drain_class_for_session(unresolvable_alias)


def test_fixer_no_substring_fallback_requires_explicit_drain_class() -> None:
    """'fixer' has no explicit drain_class; substring inference is gone."""
    with pytest.raises(PolicyValidationError):
        drain_class_for_session("fixer")


class TestNewCapabilities:
    """Tests for new workspace capabilities."""

    def test_lookup_workspace_metadata_read_capability(self) -> None:
        """WorkspaceMetadataRead maps to workspace.metadata_read."""
        result = lookup_ralph_capability(McpCapability.WORKSPACE_METADATA_READ)
        assert result == Capability.WORKSPACE_METADATA_READ

    def test_lookup_workspace_edit_capability(self) -> None:
        """WorkspaceEdit maps to workspace.edit."""
        result = lookup_ralph_capability(McpCapability.WORKSPACE_EDIT)
        assert result == Capability.WORKSPACE_EDIT

    def test_lookup_workspace_delete_capability(self) -> None:
        """WorkspaceDelete maps to workspace.delete."""
        result = lookup_ralph_capability(McpCapability.WORKSPACE_DELETE)
        assert result == Capability.WORKSPACE_DELETE

    def test_lookup_capability_alias_metadata_read(self) -> None:
        """workspace.metadata_read alias resolves correctly."""
        result = lookup_ralph_capability("workspace.metadata_read")
        assert result == Capability.WORKSPACE_METADATA_READ

    def test_lookup_capability_alias_edit(self) -> None:
        """workspace.edit alias resolves correctly."""
        result = lookup_ralph_capability("workspace.edit")
        assert result == Capability.WORKSPACE_EDIT

    def test_lookup_capability_alias_delete(self) -> None:
        """workspace.delete alias resolves correctly."""
        result = lookup_ralph_capability("workspace.delete")
        assert result == Capability.WORKSPACE_DELETE

    def test_lookup_nonexistent_capability_returns_none(self) -> None:
        """Unknown capability strings return None."""
        result = lookup_ralph_capability("nonexistent.capability")
        assert result is None


class TestDrainClassForSessionWithPolicy:
    """Tests for drain_class_for_session with an explicit AgentsPolicy."""

    def _agents(self, drain: str, drain_class: str) -> AgentsPolicy:
        return AgentsPolicy(
            agent_chains={"default": AgentChainConfig(agents=["agent"])},
            agent_drains={drain: AgentDrainConfig(chain="default", drain_class=drain_class)},
        )

    def test_explicit_drain_class_resolves_correctly(self) -> None:
        """Explicit drain_class='analysis' resolves to DrainClass.ANALYSIS."""
        policy = self._agents("my_custom_drain", "analysis")
        assert drain_class_for_session("my_custom_drain", policy) is DrainClass.ANALYSIS

    def test_explicit_drain_class_commit_resolves_correctly(self) -> None:
        """Explicit drain_class='commit' resolves to DrainClass.COMMIT."""
        policy = self._agents("my_commit_drain", "commit")
        assert drain_class_for_session("my_commit_drain", policy) is DrainClass.COMMIT

    def test_ambiguous_drain_without_explicit_class_raises(self) -> None:
        """Custom drain with no explicit drain_class and no substring match raises."""
        policy = AgentsPolicy(
            agent_chains={"default": AgentChainConfig(agents=["agent"])},
            agent_drains={"ambiguous": AgentDrainConfig(chain="default", drain_class=None)},
        )
        with pytest.raises(PolicyValidationError):
            drain_class_for_session("ambiguous", policy)

    def test_explicit_drain_class_overrides_substring_heuristic(self) -> None:
        """Explicit drain_class='review' wins over 'fix' substring in drain name."""
        policy = self._agents("bugfix_reviewer", "review")
        assert drain_class_for_session("bugfix_reviewer", policy) is DrainClass.REVIEW

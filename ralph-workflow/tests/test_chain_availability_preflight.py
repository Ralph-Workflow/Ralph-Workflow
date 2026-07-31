"""Regression tests for the chain binary preflight."""

from __future__ import annotations

import pytest

from ralph.policy.models import AgentChainConfig, AgentDrainConfig, AgentsPolicy
from ralph.policy.validation import PolicyValidationError, validate_chain_agents_on_path


def _agents_policy() -> AgentsPolicy:
    return AgentsPolicy(
        agent_chains={"planning": AgentChainConfig(agents=["codex", "pi"])},
        agent_drains={"planning": AgentDrainConfig(chain="planning")},
    )


def test_chain_availability_regression_fails_before_spawn_when_every_entry_is_missing() -> None:
    """S-1: a used chain with no CLI on PATH names install and config fixes."""
    with pytest.raises(PolicyValidationError) as excinfo:
        validate_chain_agents_on_path(_agents_policy(), available=lambda _: False)

    message = excinfo.value.message
    assert "planning" in message
    assert "codex" in message
    assert "Install: codex:" in message
    assert "Install: codex/pi:" not in message
    assert "https://github.com/openai/codex" in message
    assert "ralph-workflow.toml" in message
    assert "ralph-workflow-agents.toml" in message
    assert "ralph --diagnose" in message


def test_chain_availability_regression_allows_available_primary_with_missing_fallback() -> None:
    """S-1: one available chain entry permits the run despite a missing fallback."""
    warnings: list[str] = []

    validate_chain_agents_on_path(
        _agents_policy(),
        available=lambda name: name == "codex",
        warn=warnings.append,
    )

    assert len(warnings) == 1
    assert "pi" in warnings[0]

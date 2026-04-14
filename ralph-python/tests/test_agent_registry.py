"""Tests for the agent registry."""

from __future__ import annotations

from ralph.agents.registry import AgentRegistry
from ralph.config.models import AgentConfig, UnifiedConfig


def test_agent_registry_registers_and_resolves_agents() -> None:
    registry = AgentRegistry()
    claude = AgentConfig(cmd="claude", output_flag="--json-stream")

    registry.register("claude", claude)

    assert registry.get("claude") == claude
    assert registry.get("missing") is None
    assert registry.list_agents() == ["claude"]
    assert registry.get_command("claude") == "claude"
    assert registry.get_command("missing") is None


def test_agent_registry_from_config_loads_all_agents() -> None:
    config = UnifiedConfig(
        agents={
            "claude": AgentConfig(cmd="claude"),
            "opencode": AgentConfig(cmd="opencode", can_commit=True),
        }
    )

    registry = AgentRegistry.from_config(config)

    assert registry.list_agents() == ["claude", "opencode"]
    assert registry.get("opencode") == AgentConfig(cmd="opencode", can_commit=True)


def test_agent_registry_validate_reports_missing_required_fields() -> None:
    registry = AgentRegistry()
    registry.register("missing-cmd", AgentConfig.model_construct(cmd="", output_flag="--json-stream"))
    registry.register("missing-output", AgentConfig.model_construct(cmd="claude", output_flag=""))

    assert registry.validate() == [
        "Agent 'missing-cmd' has no command configured",
        "Agent 'missing-output' has no output flag configured",
    ]

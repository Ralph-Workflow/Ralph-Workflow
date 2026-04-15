"""Tests for the agent registry."""

from __future__ import annotations

import pytest

from ralph.agents.registry import AgentRegistry
from ralph.config.enums import AgentTransport
from ralph.config.models import AgentConfig, CcsAliasConfig, UnifiedConfig


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

    assert registry.list_agents() == ["claude", "codex", "opencode"]
    assert registry.get("opencode") == AgentConfig(cmd="opencode", can_commit=True)


def test_agent_registry_validate_reports_missing_required_fields() -> None:
    registry = AgentRegistry()
    registry.register(
        "missing-cmd", AgentConfig.model_construct(cmd="", output_flag="--json-stream")
    )
    registry.register("missing-output", AgentConfig.model_construct(cmd="claude", output_flag=""))

    assert registry.validate() == [
        "Agent 'missing-cmd' has no command configured",
        "Agent 'missing-output' has no output flag configured",
    ]


def test_agent_registry_from_config_includes_builtin_agents() -> None:
    config = UnifiedConfig()

    registry = AgentRegistry.from_config(config)

    claude = registry.get("claude")
    codex = registry.get("codex")
    opencode = registry.get("opencode")

    assert claude is not None
    assert codex is not None
    assert opencode is not None
    assert claude.cmd == "claude -p"
    assert claude.yolo_flag == "--permission-mode auto"
    assert claude.transport == AgentTransport.CLAUDE
    assert codex.cmd == "codex exec"
    assert codex.output_flag == "--json"
    assert codex.yolo_flag == "--dangerously-bypass-approvals-and-sandbox"
    assert codex.transport == AgentTransport.CODEX
    assert opencode.yolo_flag is None
    assert opencode.transport == AgentTransport.OPENCODE


def test_agent_registry_resolves_string_ccs_alias_with_defaults() -> None:
    config = UnifiedConfig(ccs_aliases={"glm": "ccs glm"})

    registry = AgentRegistry.from_config(config)
    ccs_agent = registry.get("ccs/glm")

    assert ccs_agent is not None
    assert ccs_agent.cmd == "ccs glm"
    assert ccs_agent.output_flag == config.ccs.output_flag
    assert ccs_agent.yolo_flag == "--permission-mode auto"
    assert ccs_agent.print_flag == config.ccs.print_flag
    assert ccs_agent.streaming_flag == config.ccs.streaming_flag
    assert ccs_agent.transport == AgentTransport.CLAUDE


def test_agent_registry_resolves_table_ccs_alias_with_overrides() -> None:
    config = UnifiedConfig(
        ccs_aliases={
            "work": CcsAliasConfig(
                cmd="ccs work",
                output_flag="--json-stream",
                verbose_flag="--vv",
                model_flag="--model custom",
                can_commit=False,
            )
        }
    )

    registry = AgentRegistry.from_config(config)
    ccs_agent = registry.get("ccs/work")

    assert ccs_agent is not None
    assert ccs_agent.cmd == "ccs work"
    assert ccs_agent.output_flag == "--json-stream"
    assert ccs_agent.verbose_flag == "--vv"
    assert ccs_agent.model_flag == "--model custom"
    assert ccs_agent.can_commit is False


def test_agent_registry_resolves_direct_opencode_model_reference() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("opencode/minimax/MiniMax-M2.7-highspeed")

    assert agent is not None
    assert agent.cmd == "opencode"
    assert agent.output_flag == "--json-stream"
    assert agent.json_parser == "opencode"
    assert agent.model_flag == "-m minimax/MiniMax-M2.7-highspeed"
    assert agent.can_commit is True


@pytest.mark.parametrize(
    "name",
    [
        "opencode/",
        "opencode//model",
    ],
)
def test_agent_registry_rejects_malformed_direct_opencode_reference(name: str) -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    assert registry.get(name) is None

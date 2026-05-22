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

    assert set(registry.list_agents()) >= {"claude", "claude-headless", "codex", "opencode"}
    assert registry.get("opencode") == AgentConfig(cmd="opencode", can_commit=True)


def test_builtin_claude_agent_is_claude_interactive_transport() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    claude = registry.get("claude")

    assert claude is not None
    assert claude.cmd == "claude"
    assert claude.transport == AgentTransport.CLAUDE_INTERACTIVE


def test_builtin_claude_headless_agent_is_claude_transport() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    claude_headless = registry.get("claude-headless")

    assert claude_headless is not None
    assert claude_headless.cmd == "claude -p"
    assert claude_headless.transport == AgentTransport.CLAUDE
    assert claude_headless.output_flag == "--output-format=stream-json"


def test_agent_registry_validate_reports_missing_required_fields() -> None:
    registry = AgentRegistry()
    registry.register(
        "missing-cmd", AgentConfig.model_construct(cmd="", output_flag="--json-stream")
    )
    registry.register(
        "missing-output",
        AgentConfig.model_construct(
            cmd="claude -p",
            output_flag="",
            transport=AgentTransport.CLAUDE,
        ),
    )

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
    assert claude.cmd == "claude"
    assert claude.yolo_flag == "--dangerously-skip-permissions"
    assert claude.transport == AgentTransport.CLAUDE_INTERACTIVE
    claude_headless = registry.get("claude-headless")
    assert claude_headless is not None
    assert claude_headless.cmd == "claude -p"
    assert claude_headless.transport == AgentTransport.CLAUDE
    assert codex.cmd == "codex exec"
    assert codex.output_flag == "--json"
    assert codex.yolo_flag == "--dangerously-bypass-approvals-and-sandbox"
    assert codex.transport == AgentTransport.CODEX
    assert opencode.yolo_flag is None
    assert opencode.transport == AgentTransport.OPENCODE

    agy = registry.get("agy")
    assert agy is not None
    assert agy.cmd == "agy"
    assert agy.transport == AgentTransport.AGY
    assert agy.yolo_flag == "--dangerously-skip-permissions"
    assert agy.print_flag == "--print"
    assert agy.session_flag == "--conversation {}"


def test_ccs_alias_keeps_claude_transport() -> None:
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
    assert ccs_agent.transport == AgentTransport.CLAUDE


def test_agent_registry_resolves_direct_opencode_model_reference() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("opencode/minimax/MiniMax-M2.7-highspeed")

    assert agent is not None
    assert agent.cmd == "opencode"
    assert agent.output_flag == "--json-stream"
    assert agent.json_parser == "opencode"
    assert agent.model_flag == "-m minimax/MiniMax-M2.7-highspeed"
    assert agent.can_commit is True


def test_claude_model_reference_resolves_to_claude_interactive() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("claude/opus")

    assert agent is not None
    assert agent.cmd == "claude"
    assert agent.output_flag is None
    assert agent.json_parser == "claude"
    assert agent.transport == AgentTransport.CLAUDE_INTERACTIVE
    assert agent.model_flag == "--model opus"
    assert agent.can_commit is True


def test_agent_registry_resolves_direct_claude_model_reference() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("claude/opus")

    assert agent is not None
    assert agent.cmd == "claude"
    assert agent.output_flag is None
    assert agent.json_parser == "claude"
    assert agent.transport == AgentTransport.CLAUDE_INTERACTIVE
    assert agent.model_flag == "--model opus"
    assert agent.can_commit is True


def test_agent_config_claude_cmd_infers_claude_interactive() -> None:
    config = AgentConfig(cmd="claude")

    assert config.transport == AgentTransport.CLAUDE_INTERACTIVE


def test_claude_headless_model_reference_resolves() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("claude-headless/haiku")

    assert agent is not None
    assert agent.cmd == "claude -p"
    assert agent.output_flag == "--output-format=stream-json"
    assert agent.transport == AgentTransport.CLAUDE
    assert agent.model_flag == "--model haiku"


def test_registry_validate_exempts_claude_interactive_output_flag() -> None:
    registry = AgentRegistry()
    registry.register(
        "interactive",
        AgentConfig(
            cmd="claude",
            output_flag=None,
            transport=AgentTransport.CLAUDE_INTERACTIVE,
        ),
    )

    assert registry.validate() == []


def test_agent_registry_resolves_direct_ccs_model_reference() -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    agent = registry.get("ccs/mm")

    assert agent is not None
    assert agent.cmd == "ccs mm"
    assert agent.output_flag == "--output-format=stream-json"
    assert agent.yolo_flag == "--permission-mode auto"
    assert agent.verbose_flag == "--verbose"
    assert agent.json_parser == "claude"
    assert agent.transport == AgentTransport.CLAUDE
    assert agent.print_flag == "--print"
    assert agent.streaming_flag == "--include-partial-messages"
    assert agent.session_flag == "--resume {}"
    assert agent.can_commit is True


@pytest.mark.parametrize(
    "name",
    [
        "opencode/",
        "opencode//model",
        "claude/",
        "claude//model",
    ],
)
def test_agent_registry_rejects_malformed_direct_opencode_reference(name: str) -> None:
    registry = AgentRegistry.from_config(UnifiedConfig())

    assert registry.get(name) is None

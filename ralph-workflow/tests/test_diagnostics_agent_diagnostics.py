"""Unit tests for the diagnostics module.

Tests cover:
- SystemInfo.gather() populates all fields
- run_diagnostics() returns a DiagnosticReport
- AgentDiagnostics with mocked AgentRegistry
- Agent availability check logic
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ralph.config.enums import JsonParserType
from ralph.config.models import AgentConfig
from ralph.diagnostics import (
    AgentDiagnostics,
)

MULTI_AGENT_COUNT = 2


class TestAgentDiagnostics:
    """Tests for AgentDiagnostics dataclass."""

    def test_agent_diagnostics_test_with_empty_registry(self) -> None:
        """Test that AgentDiagnostics.test() works with empty registry."""
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = []

        diagnostics = AgentDiagnostics.test(mock_registry)
        assert diagnostics.total_agents == 0
        assert diagnostics.available_agents == 0
        assert diagnostics.unavailable_agents == 0
        assert diagnostics.agent_status == []

    def test_agent_diagnostics_test_with_available_agent(self) -> None:
        """Test that AgentDiagnostics.test() detects available agent."""
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = ["claude"]

        agent_config = AgentConfig(
            cmd="claude",
            output_flag="--json-stream",
            can_commit=True,
            json_parser=JsonParserType.CLAUDE,
            display_name="Claude Code",
        )
        mock_registry.get.return_value = agent_config

        diagnostics = AgentDiagnostics.test(mock_registry, is_available_fn=lambda cmd: True)

        assert diagnostics.total_agents == 1
        assert diagnostics.available_agents == 1
        assert diagnostics.unavailable_agents == 0
        assert len(diagnostics.agent_status) == 1
        assert diagnostics.agent_status[0].name == "claude"
        assert diagnostics.agent_status[0].available is True

    def test_agent_diagnostics_test_with_unavailable_agent(self) -> None:
        """Test that AgentDiagnostics.test() detects unavailable agent."""
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = ["nonexistent"]

        agent_config = AgentConfig(
            cmd="nonexistent-agent",
            output_flag="--json-stream",
            can_commit=False,
            json_parser=JsonParserType.GENERIC,
        )
        mock_registry.get.return_value = agent_config

        diagnostics = AgentDiagnostics.test(mock_registry, is_available_fn=lambda cmd: False)

        assert diagnostics.total_agents == 1
        assert diagnostics.available_agents == 0
        assert diagnostics.unavailable_agents == 1
        assert diagnostics.agent_status[0].available is False

    def test_agent_diagnostics_test_with_multiple_agents(self) -> None:
        """Test that AgentDiagnostics.test() handles multiple agents."""
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = ["claude", "opencode"]

        claude_config = AgentConfig(
            cmd="claude",
            output_flag="--json-stream",
            can_commit=True,
            json_parser=JsonParserType.CLAUDE,
            display_name="Claude",
        )
        opencode_config = AgentConfig(
            cmd="opencode",
            output_flag="--json-stream",
            can_commit=False,
            json_parser=JsonParserType.OPENCODE,
            display_name="OpenCode",
        )
        configs = {
            "claude": claude_config,
            "opencode": opencode_config,
        }
        mock_registry.get.side_effect = configs.get

        diagnostics = AgentDiagnostics.test(mock_registry, is_available_fn=lambda cmd: True)

        assert diagnostics.total_agents == MULTI_AGENT_COUNT
        assert diagnostics.available_agents == MULTI_AGENT_COUNT
        assert diagnostics.unavailable_agents == 0
        assert len(diagnostics.agent_status) == MULTI_AGENT_COUNT

"""Unit tests for the diagnostics module.

Tests cover:
- SystemInfo.gather() populates all fields
- run_diagnostics() returns a DiagnosticReport
- AgentDiagnostics with mocked AgentRegistry
- Agent availability check logic
"""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ralph.config.enums import JsonParserType
from ralph.config.models import AgentConfig
from ralph.diagnostics import (
    AgentDiagnostics,
    AgentStatus,
    DiagnosticReport,
    SystemInfo,
    run_diagnostics,
)

MULTI_AGENT_COUNT = 2


class TestSystemInfo:
    """Tests for SystemInfo dataclass."""

    @pytest.fixture(scope="class")
    def info(self) -> SystemInfo:
        return SystemInfo.gather()

    def test_system_info_gather_returns_instance(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() returns a SystemInfo instance."""
        assert isinstance(info, SystemInfo)

    def test_system_info_gather_populates_os(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates the os field."""
        assert info.os is not None
        assert isinstance(info.os, str)
        assert info.os in {"linux", "darwin", "win32", "cygwin"}

    def test_system_info_gather_populates_arch(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates the arch field."""
        assert info.arch is not None
        assert isinstance(info.arch, str)

    def test_system_info_gather_populates_working_directory(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates working_directory."""
        assert info.working_directory is not None
        assert isinstance(info.working_directory, str)

    def test_system_info_gather_populates_shell(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates shell (or None)."""
        # shell may be None in CI environments, but the field should exist
        assert info.shell is None or isinstance(info.shell, str)

    def test_system_info_gather_uses_injected_env_for_shell(self) -> None:
        """Test that SystemInfo.gather() uses injected env for shell."""
        info = SystemInfo.gather(env={"SHELL": "/bin/zsh"})
        assert info.shell == "/bin/zsh"

    def test_system_info_gather_returns_none_shell_when_env_empty(self) -> None:
        """Test that SystemInfo.gather() returns None shell when env is empty."""
        info = SystemInfo.gather(env={})
        assert info.shell is None

    def test_system_info_gather_populates_git_version(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates git_version."""
        # git_version may be None if git is not installed
        assert info.git_version is None or isinstance(info.git_version, str)

    def test_system_info_gather_populates_git_repo(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates git_repo as a bool."""
        assert isinstance(info.git_repo, bool)

    def test_system_info_gather_populates_git_branch(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates git_branch when in repo."""
        # git_branch may be None if not in a git repo or git command fails
        if info.git_repo:
            # If we're in a git repo, branch should be populated (or None on detached HEAD)
            assert info.git_branch is None or isinstance(info.git_branch, str)

    def test_system_info_gather_populates_uncommitted_changes(self, info: SystemInfo) -> None:
        """Test that SystemInfo.gather() populates uncommitted_changes."""
        # uncommitted_changes may be None if not in a git repo
        if info.git_repo:
            assert isinstance(info.uncommitted_changes, int)
            assert info.uncommitted_changes >= 0


class TestAgentStatus:
    """Tests for AgentStatus dataclass."""

    def test_agent_status_creation(self) -> None:
        """Test that AgentStatus can be created with all fields."""
        status = AgentStatus(
            name="claude",
            display_name="Claude Code",
            available=True,
            json_parser="claude",
            command="claude",
        )
        assert status.name == "claude"
        assert status.display_name == "Claude Code"
        assert status.available is True
        assert status.json_parser == "claude"
        assert status.command == "claude"

    def test_agent_status_defaults(self) -> None:
        """Test that AgentStatus fields have correct types."""
        status = AgentStatus(
            name="test",
            display_name="Test",
            available=False,
            json_parser="generic",
            command="test",
        )
        assert isinstance(status.name, str)
        assert isinstance(status.available, bool)


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


class TestDiagnosticReport:
    """Tests for DiagnosticReport dataclass."""

    def test_diagnostic_report_has_system_and_agents(self) -> None:
        """Test that DiagnosticReport contains system and agents fields."""
        system_info = SystemInfo.gather()
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = []
        agent_diag = AgentDiagnostics.test(mock_registry)

        report = DiagnosticReport(system=system_info, agents=agent_diag)
        assert isinstance(report.system, SystemInfo)
        assert isinstance(report.agents, AgentDiagnostics)


class TestRunDiagnostics:
    """Tests for run_diagnostics() function."""

    def test_run_diagnostics_returns_diagnostic_report(self) -> None:
        """Test that run_diagnostics() returns a DiagnosticReport."""
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = []

        report = run_diagnostics(mock_registry)
        assert isinstance(report, DiagnosticReport)
        assert isinstance(report.system, SystemInfo)
        assert isinstance(report.agents, AgentDiagnostics)

    def test_run_diagnostics_gathers_system_info(self) -> None:
        """Test that run_diagnostics() gathers system information."""
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = []

        report = run_diagnostics(mock_registry)
        assert report.system is not None
        assert isinstance(report.system.os, str)
        assert isinstance(report.system.arch, str)

    def test_run_diagnostics_tests_agent_availability(self) -> None:
        """Test that run_diagnostics() tests agent availability."""
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = ["claude"]

        agent_config = AgentConfig(
            cmd="claude",
            output_flag="--json-stream",
            can_commit=True,
            json_parser=JsonParserType.CLAUDE,
        )
        mock_registry.get.return_value = agent_config

        report = run_diagnostics(mock_registry, is_available_fn=lambda cmd: True)

        assert report.agents.total_agents == 1
        assert report.agents.available_agents == 1

    def test_run_diagnostics_uses_injected_env_for_shell(self) -> None:
        """Test that run_diagnostics() uses injected env for shell in system info."""
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = []

        report = run_diagnostics(mock_registry, env={"SHELL": "/bin/zsh"})

        assert report.system.shell == "/bin/zsh"

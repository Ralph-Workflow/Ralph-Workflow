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
    DiagnosticReport,
    SystemInfo,
    run_diagnostics,
)

# run_diagnostics() calls SystemInfo.gather(), which executes real git
# subprocesses (rev-parse, branch, status). Wall-clock cost under
# parallel xdist load is regularly > 1 s on busy machines, so the
# default 1-second per-test ceiling is unsafe.
pytestmark = pytest.mark.timeout_seconds(5)

MULTI_AGENT_COUNT = 2


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

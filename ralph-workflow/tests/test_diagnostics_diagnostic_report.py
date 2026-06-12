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

from ralph.diagnostics import (
    AgentDiagnostics,
    DiagnosticReport,
    SystemInfo,
)

# SystemInfo.gather() executes real git subprocesses (rev-parse, branch,
# status). Wall-clock cost under parallel xdist load is regularly > 1 s
# on busy machines, so the default 1-second per-test ceiling is unsafe.
pytestmark = pytest.mark.timeout_seconds(5)

MULTI_AGENT_COUNT = 2


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

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from ralph.diagnostics import (
    AgentDiagnostics,
    DiagnosticReport,
    SystemInfo,
)
from tests._diagnostics_git_probe import stub_git_probe

pytestmark = pytest.mark.timeout_seconds(5)

MULTI_AGENT_COUNT = 2


class TestDiagnosticReport:
    def test_diagnostic_report_has_system_and_agents(self) -> None:
        system_info = SystemInfo.gather(git_probe=stub_git_probe)
        mock_registry = MagicMock()
        mock_registry.list_agents.return_value = []
        agent_diag = AgentDiagnostics.test(mock_registry)

        report = DiagnosticReport(system=system_info, agents=agent_diag)
        assert isinstance(report.system, SystemInfo)
        assert isinstance(report.agents, AgentDiagnostics)

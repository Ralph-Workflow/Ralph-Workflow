from __future__ import annotations

from ralph.diagnostics import (
    AgentStatus,
)

MULTI_AGENT_COUNT = 2


class TestAgentStatus:
    def test_agent_status_creation(self) -> None:
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
        status = AgentStatus(
            name="test",
            display_name="Test",
            available=False,
            json_parser="generic",
            command="test",
        )
        assert isinstance(status.name, str)
        assert isinstance(status.available, bool)

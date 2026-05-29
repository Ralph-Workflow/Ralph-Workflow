"""Phase visibility integration tests for unsafe_exec and raw_exec MCP tools.

Verifies that unsafe_exec and raw_exec are visible only in development and fix drains,
and not visible in planning, analysis, review, commit, or analysis-commit drains.
Exercises the full pipeline: DEFAULT_CAPABILITIES -> CapabilitySet ->
visible_mcp_tool_names -> ToolBridge._is_tool_allowed -> list_definitions().
"""

from __future__ import annotations

import pytest

from ralph.config.mcp_models import McpConfig
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.mcp.tools.names import RAW_EXEC_TOOL, UNSAFE_EXEC_TOOL
from ralph.prompts.template_variables import DEFAULT_CAPABILITIES


def _make_session(drain_str: str) -> AgentSession:
    session_drain = SessionDrain(drain_str)
    default_caps = DEFAULT_CAPABILITIES.get(session_drain, ())
    return AgentSession(
        session_id=f"test-{drain_str}",
        run_id="run-1",
        drain=drain_str,
        capabilities={cap.value for cap in default_caps},
    )


_WRITE_DRAINS = (
    SessionDrain.DEVELOPMENT,
    SessionDrain.FIX,
)

_NON_WRITE_DRAINS = [d for d in SessionDrain if d not in _WRITE_DRAINS]


class TestUnsafeExecPhaseVisibility:
    @pytest.mark.parametrize("drain", [d.value for d in _WRITE_DRAINS])
    def test_unsafe_exec_and_raw_exec_visible_for_write_drain(self, drain: str) -> None:
        session = _make_session(drain)
        registry = build_ralph_tool_registry(session, workspace=None, mcp_config=McpConfig())
        tool_names = {t.name for t in registry.list_definitions()}
        for tool, label in ((UNSAFE_EXEC_TOOL, "unsafe_exec"), (RAW_EXEC_TOOL, "raw_exec")):
            assert tool in tool_names, (
                f"{label} not in tools for drain {drain}; got: {sorted(tool_names)}"
            )

    @pytest.mark.parametrize("drain", [d.value for d in _NON_WRITE_DRAINS])
    def test_unsafe_exec_and_raw_exec_not_visible_for_non_write_drain(self, drain: str) -> None:
        session = _make_session(drain)
        registry = build_ralph_tool_registry(session, workspace=None, mcp_config=McpConfig())
        tool_names = {t.name for t in registry.list_definitions()}
        for tool, label in ((UNSAFE_EXEC_TOOL, "unsafe_exec"), (RAW_EXEC_TOOL, "raw_exec")):
            assert tool not in tool_names, (
                f"{label} unexpectedly visible for drain {drain}; got: {sorted(tool_names)}"
            )

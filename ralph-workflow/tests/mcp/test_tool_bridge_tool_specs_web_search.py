"""Tests for ralph.mcp.tool_bridge — T12 extensibility tests."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from ralph.config.mcp_models import McpConfig, WebSearchConfig
from ralph.mcp.artifacts.plan import PLAN_SECTION_NAMES
from ralph.mcp.tools.bridge import tool_specs
from ralph.mcp.tools.names import (
    ALL_RALPH_TOOLS,
    SUBMIT_PLAN_SECTION_TOOL,
    WEB_SEARCH_TOOL,
)
from ralph.mcp.upstream.models import UpstreamTool

if TYPE_CHECKING:
    from ralph.mcp.upstream.config import (
        UpstreamMcpServer,
    )


class _FakeUpstreamClientFactory:
    _tools: list[UpstreamTool]

    def __init__(self, tools: list[dict[str, object]]) -> None:
        result: list[UpstreamTool] = []
        for t in tools:
            name = cast("str", t["name"])
            desc_raw = t.get("description", "")
            desc = str(desc_raw) if desc_raw else ""
            input_schema_raw = t.get("inputSchema", {})
            input_schema = cast("dict[str, object]", input_schema_raw)
            result.append(UpstreamTool(name=name, description=desc, input_schema=input_schema))
        self._tools = result

    def __call__(self, server: UpstreamMcpServer) -> MagicMock:
        mock = MagicMock()
        object.__setattr__(mock.list_tools, "return_value", self._tools)
        return mock


class _AllowedSession:
    session_id = "test-session"

    def check_capability(self, capability: str) -> object:
        return "approved"


class _FakeWorkspace:
    def absolute_path(self, path: str) -> str:
        return path


class TestToolSpecsWebSearch:
    """T12.1-T12.4: web_search tool in tool_specs()."""

    def test_web_search_in_tool_specs_when_enabled(self) -> None:
        """When McpConfig has web_search.enabled=True, web_search tool appears in specs."""
        config = McpConfig(web_search=WebSearchConfig(enabled=True))
        specs = tool_specs(config)
        tool_names = {spec.metadata.definition.name for spec in specs}
        assert WEB_SEARCH_TOOL in tool_names

    def test_web_search_not_in_tool_specs_when_disabled(self) -> None:
        """When enabled=False, web_search tool does NOT appear."""
        config = McpConfig(web_search=WebSearchConfig(enabled=False))
        specs = tool_specs(config)
        tool_names = {spec.metadata.definition.name for spec in specs}
        assert WEB_SEARCH_TOOL not in tool_names

    def test_tool_specs_signature_accepts_mcp_config(self) -> None:
        """Verify tool_specs(mcp_config) signature works."""
        config = McpConfig()
        specs = tool_specs(config)
        assert isinstance(specs, tuple)
        assert len(specs) > 0

    def test_all_existing_tool_specs_still_present(self) -> None:
        """Regression: no existing tools removed from tool_specs."""
        config = McpConfig()
        specs = tool_specs(config)
        tool_names = {spec.metadata.definition.name for spec in specs}
        for tool in ALL_RALPH_TOOLS:
            assert tool in tool_names, f"Tool {tool} is missing from tool_specs"

    def test_submit_plan_section_tool_lists_every_supported_plan_section(self) -> None:
        """The MCP-facing tool description must stay aligned with the plan schema.

        planning_edit.jinja teaches agents to revise plans through the MCP plan-edit
        flow, so the brokered tool metadata must enumerate every section accepted by
        the backend validator.
        """
        specs = tool_specs(McpConfig())
        submit_plan_spec = next(
            spec for spec in specs if spec.metadata.definition.name == SUBMIT_PLAN_SECTION_TOOL
        )
        description = submit_plan_spec.metadata.definition.description

        for section_name in sorted(PLAN_SECTION_NAMES):
            assert section_name in description, (
                f"submit-plan-section description is missing schema-supported section "
                f"{section_name!r}"
            )



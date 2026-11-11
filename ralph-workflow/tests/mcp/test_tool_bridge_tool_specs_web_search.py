from __future__ import annotations

from ralph.config.mcp_models import McpConfig, WebSearchConfig
from ralph.mcp.tools.bridge import tool_specs
from ralph.mcp.tools.names import (
    ALL_RALPH_TOOLS,
    WEB_SEARCH_TOOL,
)


class TestToolSpecsWebSearch:
    def test_web_search_in_tool_specs_when_enabled(self) -> None:
        config = McpConfig(web_search=WebSearchConfig(enabled=True))
        specs = tool_specs(config)
        tool_names = {spec.metadata.definition.name for spec in specs}
        assert WEB_SEARCH_TOOL in tool_names

    def test_web_search_not_in_tool_specs_when_disabled(self) -> None:
        config = McpConfig(web_search=WebSearchConfig(enabled=False))
        specs = tool_specs(config)
        tool_names = {spec.metadata.definition.name for spec in specs}
        assert WEB_SEARCH_TOOL not in tool_names

    def test_tool_specs_signature_accepts_mcp_config(self) -> None:
        config = McpConfig()
        specs = tool_specs(config)
        assert isinstance(specs, tuple)
        assert len(specs) > 0

    def test_all_existing_tool_specs_still_present(self) -> None:
        config = McpConfig()
        specs = tool_specs(config)
        tool_names = {spec.metadata.definition.name for spec in specs}
        for tool in ALL_RALPH_TOOLS:
            assert tool in tool_names, f"Tool {tool} is missing from tool_specs"

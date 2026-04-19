from __future__ import annotations

from ralph.mcp.tool_names import ALL_RALPH_TOOLS, WEB_SEARCH_TOOL, WEB_SEARCH_TOOLS, RalphToolName


def test_web_search_tool_name_is_in_enum() -> None:
    assert RalphToolName.WEB_SEARCH == "web_search"


def test_web_search_tool_constant_matches_enum() -> None:
    assert WEB_SEARCH_TOOL == RalphToolName.WEB_SEARCH


def test_web_search_tools_group_contains_web_search() -> None:
    assert WEB_SEARCH_TOOLS == (WEB_SEARCH_TOOL,)


def test_all_ralph_tools_includes_web_search_tool() -> None:
    assert WEB_SEARCH_TOOL in ALL_RALPH_TOOLS

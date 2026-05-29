from __future__ import annotations

from ralph.mcp.tools.names import (
    ALL_RALPH_TOOLS,
    PROCESS_EXEC_UNBOUNDED_TOOLS,
    UNSAFE_EXEC_TOOL,
    WEB_SEARCH_TOOL,
    WEB_SEARCH_TOOLS,
    RalphToolName,
)


def test_web_search_tool_name_is_in_enum() -> None:
    assert RalphToolName.WEB_SEARCH == "web_search"


def test_web_search_tool_constant_matches_enum() -> None:
    assert WEB_SEARCH_TOOL == RalphToolName.WEB_SEARCH


def test_web_search_tools_group_contains_web_search() -> None:
    assert WEB_SEARCH_TOOLS == (WEB_SEARCH_TOOL,)


def test_all_ralph_tools_includes_web_search_tool() -> None:
    assert WEB_SEARCH_TOOL in ALL_RALPH_TOOLS


def test_unsafe_exec_tool_name_is_in_enum() -> None:
    assert RalphToolName.UNSAFE_EXEC == "unsafe_exec"


def test_unsafe_exec_tool_constant_matches_enum() -> None:
    assert UNSAFE_EXEC_TOOL == RalphToolName.UNSAFE_EXEC


def test_process_exec_unbounded_tools_group_contains_unsafe_exec() -> None:
    assert PROCESS_EXEC_UNBOUNDED_TOOLS == (UNSAFE_EXEC_TOOL,)


def test_all_ralph_tools_includes_unsafe_exec_tool() -> None:
    assert UNSAFE_EXEC_TOOL in ALL_RALPH_TOOLS

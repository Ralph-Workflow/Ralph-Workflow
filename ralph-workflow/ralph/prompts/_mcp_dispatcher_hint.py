
from __future__ import annotations

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME

__all__ = ["agy_dispatcher_hint_text"]


def agy_dispatcher_hint_text(
    target_tool_name: str, *, server_name: str = RALPH_MCP_SERVER_NAME
) -> str:
    return (
        f"- `{target_tool_name}` is a Ralph Workflow MCP tool; AGY does "
        "not list it directly as a callable tool name. Call your "
        f"`call_mcp_tool` tool, naming MCP server `{server_name}` and "
        f"target tool `{target_tool_name}` to reach it. This is your "
        "first and required attempt -- do not skip straight to the "
        "file-fallback path because the tool name isn't directly listed. "
        "Only fall back to writing the markdown directly when "
        f"`call_mcp_tool` itself errors when targeting server "
        f"`{server_name}` (for example, the server is unreachable) -- "
        "not because the target tool's name is unfamiliar. Ralph "
        "Workflow validates and promotes a direct file write as a "
        "fallback, but a genuine `call_mcp_tool` attempt against the "
        f"`{server_name}` server always comes first. Do not write the "
        "canonical artifact directly without first attempting "
        "`call_mcp_tool`."
    )

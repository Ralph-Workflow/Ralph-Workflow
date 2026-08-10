"""Shared AGY MCP-dispatcher hint text for agent-facing prompts.

Brief ``.agent/PRODUCT_CRITERIA.md`` C1 / DoD 13 -- a transport
fix that only the smoke prompt knows about is not a transport fix.
The route to Ralph's tools (the ``call_mcp_tool`` dispatcher) must
be named in *every* agent-facing prompt that asks the model to call
``ralph_submit_md_artifact`` or ``declare_complete``, not only the
smoke prompt.

AGY does not list Ralph's tools directly: its init frame advertises
only the generic ``call_mcp_tool`` dispatcher (confirmed by a live
v1.1.10 capture, never guessed -- see
``tests/display/_fixtures/agy_wire_provenance.md``). A plain "call
``{tool}``" bullet plus a permissive "if unavailable, write the file
instead" fallback bullet measurably teaches the model nothing: a live
v1.1.10 run against that phrasing still took the fallback-file path
on every turn (the 2026-08-05-shaped defect this branch replaces).

This module extracts the AGY branch text (named here so both the
smoke prompt and the master prompt call it instead of inlining it)
and parameterizes it by the target tool name so it can render the
hint for ``ralph_submit_md_artifact`` (planning/master prompt) as
well as whatever tool name each other phase's prompt already names.

The exact JSON argument shape for ``call_mcp_tool`` itself is part
of AGY's own tool schema (already visible to the model), so it is
deliberately not hand-typed here -- doing so would risk asserting an
unmeasured shape.
"""

from __future__ import annotations

from ralph.mcp.tools.names import RALPH_MCP_SERVER_NAME

__all__ = ["agy_dispatcher_hint_text"]


def agy_dispatcher_hint_text(target_tool_name: str, *, server_name: str = RALPH_MCP_SERVER_NAME) -> str:
    """Return the AGY-shaped MCP-dispatcher hint for one target tool.

    The result is the bullet text naming ``call_mcp_tool`` as the only
    first attempt, naming the MCP server, and naming the target tool.
    Callers (smoke prompt, master prompt, any phase prompt that asks
    the model to call a Ralph tool) insert this text into the prompt
    when the transport is ``AgentTransport.AGY``; non-AGY transports
    do NOT get this hint (their tools advertise ``ralph_*`` directly,
    so the dispatcher is unreachable for them).

    Args:
        target_tool_name: The Ralph MCP tool the model is being asked
            to call -- e.g. ``ralph_submit_md_artifact`` (master prompt)
            or whatever artifact-submit tool name the smoke prompt uses.
        server_name: The MCP server name to route through. Defaults to
            ``RALPH_MCP_SERVER_NAME`` (the canonical ralph server name).
            Tests may inject an alternate value.
    """
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

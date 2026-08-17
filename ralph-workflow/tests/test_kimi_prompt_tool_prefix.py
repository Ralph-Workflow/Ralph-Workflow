"""Kimi prompt tool-name prefix follows the Claude-style convention (PA-005).

Kimi Code exposes remote MCP tools as ``mcp__<server>__<tool>`` (the same
shape Claude / Codex / Cursor use), so ``AgentTransport.KIMI`` belongs in
``_CLAUDE_STYLE_TRANSPORTS`` and the prompt materializers must return the
``mcp__ralph__``-prefixed tool names.  Without the entry the kimi prompts
would render unprefixed tool names and the model would call a tool that
does not exist on its wire.
"""

from __future__ import annotations

from ralph.config.enums import AgentTransport
from ralph.mcp.tools.names import (
    SUBMIT_MD_ARTIFACT_TOOL,
    claude_tool_name,
    claude_tool_name_prefix,
)
from ralph.prompts.materialize import (
    submit_artifact_tool_name_for_transport,
    tool_name_prefix_for_transport,
)


def test_submit_artifact_tool_name_for_kimi_is_claude_prefixed() -> None:
    """Kimi prompts name the submit tool with the ``mcp__ralph__`` prefix."""
    assert submit_artifact_tool_name_for_transport(AgentTransport.KIMI) == (
        claude_tool_name(SUBMIT_MD_ARTIFACT_TOOL)
    )
    assert submit_artifact_tool_name_for_transport(AgentTransport.KIMI).startswith(
        claude_tool_name_prefix()
    )


def test_tool_name_prefix_for_kimi_is_mcp_dunder() -> None:
    """The kimi tool-name prefix is the Claude-style ``mcp__ralph__`` string."""
    assert tool_name_prefix_for_transport(AgentTransport.KIMI) == claude_tool_name_prefix()
    assert tool_name_prefix_for_transport(AgentTransport.KIMI) == "mcp__ralph__"


def test_existing_claude_style_transports_keep_the_contract() -> None:
    """Snapshot regression: the four pre-existing members stay Claude-styled."""
    for transport in (
        AgentTransport.CLAUDE,
        AgentTransport.CLAUDE_INTERACTIVE,
        AgentTransport.CODEX,
        AgentTransport.CURSOR,
    ):
        assert submit_artifact_tool_name_for_transport(transport) == claude_tool_name(
            SUBMIT_MD_ARTIFACT_TOOL
        )
        assert tool_name_prefix_for_transport(transport) == claude_tool_name_prefix()


def test_kimi_matches_claude_naming_exactly() -> None:
    """Kimi and Claude render identical submit-artifact tool names."""
    assert submit_artifact_tool_name_for_transport(
        AgentTransport.KIMI
    ) == submit_artifact_tool_name_for_transport(AgentTransport.CLAUDE)

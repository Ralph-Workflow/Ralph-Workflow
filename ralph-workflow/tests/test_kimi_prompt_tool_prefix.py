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
    assert submit_artifact_tool_name_for_transport(AgentTransport.KIMI) == (
        claude_tool_name(SUBMIT_MD_ARTIFACT_TOOL)
    )
    assert submit_artifact_tool_name_for_transport(AgentTransport.KIMI).startswith(
        claude_tool_name_prefix()
    )


def test_tool_name_prefix_for_kimi_is_mcp_dunder() -> None:
    assert tool_name_prefix_for_transport(AgentTransport.KIMI) == claude_tool_name_prefix()
    assert tool_name_prefix_for_transport(AgentTransport.KIMI) == "mcp__ralph__"


def test_existing_claude_style_transports_keep_the_contract() -> None:
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
    assert submit_artifact_tool_name_for_transport(
        AgentTransport.KIMI
    ) == submit_artifact_tool_name_for_transport(AgentTransport.CLAUDE)

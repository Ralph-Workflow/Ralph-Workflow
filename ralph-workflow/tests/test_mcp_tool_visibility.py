"""Tests for MCP tool visibility and config-driven filtering.

These tests drive the tool registry directly, without spawning any subprocess
or opening network connections.
"""

from __future__ import annotations

import sys
from typing import TYPE_CHECKING

from ralph.config.mcp_models import McpConfig, MediaConfig, WebSearchConfig
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.tool_contract import visible_tool_names_for_capabilities
from ralph.mcp.tools.bridge import build_ralph_tool_registry

if TYPE_CHECKING:
    from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.names import WEB_SEARCH_TOOL
from ralph.mcp.transport.common import merge_mcp_toml_into_upstreams
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.workspace.memory import MemoryWorkspace

_CAPABILITIES = {
    "WorkspaceRead",
    "WorkspaceWriteAny",
    "WorkspaceMetadataRead",
    "WorkspaceEdit",
    "WorkspaceDelete",
    "GitStatusRead",
    "ProcessExecBounded",
    "ArtifactSubmit",
    "RunReportProgress",
    "EnvRead",
    "WebSearch",
    "WebVisit",
    "media.read",
}


def _make_session() -> AgentSession:
    return AgentSession(
        session_id="test-session",
        run_id="test-run",
        drain="test",
        capabilities=_CAPABILITIES,
    )


def _visible_tool_names(registry: ToolBridge) -> set[str]:
    return {d.name for d in registry.list_definitions()}


def test_default_registry_schemas_are_isolated_between_instances() -> None:
    """A caller cannot mutate the next bridge instance through its schema."""
    first = build_ralph_tool_registry(_make_session(), MemoryWorkspace())
    first_definition = first.list_definitions()[0]
    first_definition.input_schema["cache_probe"] = True

    second = build_ralph_tool_registry(_make_session(), MemoryWorkspace())
    second_definition = second.list_definitions()[0]

    assert "cache_probe" not in second_definition.input_schema


def test_prompt_catalog_reuses_profile_without_leaking_mutations() -> None:
    """Repeated prompt rendering preserves the capability-filtered catalog."""
    first = visible_tool_names_for_capabilities(_CAPABILITIES, drain="development")
    first.clear()

    second = visible_tool_names_for_capabilities(reversed(tuple(_CAPABILITIES)), drain="development")

    assert "read_file" in second
    assert WEB_SEARCH_TOOL in second


def test_disabled_web_search_omits_tool() -> None:
    config = McpConfig(web_search=WebSearchConfig(enabled=False))
    registry = build_ralph_tool_registry(_make_session(), MemoryWorkspace(), mcp_config=config)
    registry.set_client_capabilities(set())
    assert WEB_SEARCH_TOOL not in _visible_tool_names(registry)


def test_multimodal_client_sees_read_image_by_default() -> None:
    registry = build_ralph_tool_registry(_make_session(), MemoryWorkspace())
    registry.set_client_capabilities({"image", "media"})
    assert "read_image" in _visible_tool_names(registry)


def test_text_only_client_does_not_see_read_image_by_default() -> None:
    registry = build_ralph_tool_registry(_make_session(), MemoryWorkspace())
    registry.set_client_capabilities(set())
    assert "read_image" not in _visible_tool_names(registry)


def test_explicit_media_disabled_removes_read_image() -> None:
    config = McpConfig(media=MediaConfig(enabled=False))
    registry = build_ralph_tool_registry(_make_session(), MemoryWorkspace(), mcp_config=config)
    registry.set_client_capabilities({"image", "media"})
    assert "read_image" not in _visible_tool_names(registry)


def test_mcp_toml_wins_over_simulated_claude_json_collision() -> None:
    claude_native = (
        UpstreamMcpServer(name="shared", transport="http", url="https://claude.example/mcp"),
    )
    mcp_toml_servers = (
        UpstreamMcpServer(
            name="shared",
            transport="stdio",
            command=sys.executable,
            args=("custom.py",),
        ),
    )
    merged = merge_mcp_toml_into_upstreams(claude_native, mcp_toml_servers)
    assert merged == mcp_toml_servers

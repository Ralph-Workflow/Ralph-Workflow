"""Tests for ralph.mcp.tool_bridge — T12 extensibility tests."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, cast
from unittest.mock import MagicMock

from ralph.config.mcp_models import McpConfig, WebSearchConfig
from ralph.mcp.tool_bridge import _tool_specs, build_ralph_tool_registry
from ralph.mcp.tool_names import (
    ALL_RALPH_TOOLS,
    WEB_SEARCH_TOOL,
)
from ralph.mcp.upstream_config import (
    UpstreamMcpServer,
    load_upstream_mcp_servers,
    serialize_upstream_mcp_servers,
)
from ralph.mcp.upstream_models import UpstreamTool
from ralph.mcp.upstream_registry import UpstreamRegistry

if TYPE_CHECKING:
    import pytest


class _FakeUpstreamClientFactory:
    def __init__(self, tools: list[dict[str, object]]) -> None:
        self._tools: list[UpstreamTool] = [
            UpstreamTool(
                name=cast(str, t["name"]),
                description=str(t.get("description", "")) if t.get("description") else "",
                input_schema=cast(dict[str, object], t.get("inputSchema", {})),
            )
            for t in tools
        ]

    def __call__(self, server: UpstreamMcpServer) -> MagicMock:
        mock = MagicMock()
        mock.list_tools.return_value = self._tools
        return mock


class _AllowedSession:
    session_id = "test-session"

    def check_capability(self, capability: str) -> object:
        return "approved"


class _FakeWorkspace:
    def absolute_path(self, path: str) -> str:
        return path


class TestToolSpecsWebSearch:
    """T12.1-T12.4: web_search tool in _tool_specs()."""

    def test_web_search_in_tool_specs_when_enabled(self) -> None:
        """When McpConfig has web_search.enabled=True, web_search tool appears in specs."""
        config = McpConfig(web_search=WebSearchConfig(enabled=True))
        specs = _tool_specs(config)
        tool_names = {spec.metadata.definition.name for spec in specs}
        assert WEB_SEARCH_TOOL in tool_names

    def test_web_search_not_in_tool_specs_when_disabled(self) -> None:
        """When enabled=False, web_search tool does NOT appear."""
        config = McpConfig(web_search=WebSearchConfig(enabled=False))
        specs = _tool_specs(config)
        tool_names = {spec.metadata.definition.name for spec in specs}
        assert WEB_SEARCH_TOOL not in tool_names

    def test_tool_specs_signature_accepts_mcp_config(self) -> None:
        """Verify _tool_specs(mcp_config) signature works."""
        config = McpConfig()
        specs = _tool_specs(config)
        assert isinstance(specs, tuple)
        assert len(specs) > 0

    def test_all_existing_tool_specs_still_present(self) -> None:
        """Regression: no existing tools removed from _tool_specs."""
        config = McpConfig()
        specs = _tool_specs(config)
        tool_names = {spec.metadata.definition.name for spec in specs}
        for tool in ALL_RALPH_TOOLS:
            assert tool in tool_names, f"Tool {tool} is missing from _tool_specs"


class TestUpstreamEnvVar:
    """T12.5-T12.7: RALPH_UPSTREAM_MCP_CONFIG env var handling."""

    def test_upstream_proxy_tools_registered_from_env_var(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Boot with RALPH_UPSTREAM_MCP_CONFIG set to serialized fake upstream.

        Asserts that the proxied tool alias appears in the registry.
        """
        fake_upstream_server = UpstreamMcpServer(
            name="my-fake-server",
            transport="http",
            url="http://127.0.0.1:9999/mcp",
        )
        serialized = serialize_upstream_mcp_servers([fake_upstream_server])
        monkeypatch.setenv("RALPH_UPSTREAM_MCP_CONFIG", serialized)

        fake_tools = [
            {
                "name": "ping",
                "description": "Ping the server",
                "inputSchema": {"type": "object", "properties": {}},
            },
            {
                "name": "echo",
                "description": "Echo back input",
                "inputSchema": {
                    "type": "object",
                    "properties": {"msg": {"type": "string"}},
                    "required": ["msg"],
                },
            },
        ]
        mock_factory = _FakeUpstreamClientFactory(fake_tools)
        upstream_reg = UpstreamRegistry.build(
            [fake_upstream_server], client_factory=mock_factory
        )

        bridge = build_ralph_tool_registry(
            _AllowedSession(),
            _FakeWorkspace(),
            upstream_registry=upstream_reg,
        )

        proxied_aliases = {defn.name for defn in bridge.list_definitions()}
        assert "ralph_upstream__my-fake-server__ping" in proxied_aliases
        assert "ralph_upstream__my-fake-server__echo" in proxied_aliases

    def test_empty_upstream_env_var_does_not_crash_boot(
        self, monkeypatch: pytest.MonkeyPatch
    ) -> None:
        """Unset env var → server still starts with only built-in tools."""
        monkeypatch.delenv("RALPH_UPSTREAM_MCP_CONFIG", raising=False)

        bridge = build_ralph_tool_registry(
            _AllowedSession(),
            _FakeWorkspace(),
        )

        tool_names = {defn.name for defn in bridge.list_definitions()}
        for tool in ALL_RALPH_TOOLS:
            assert tool in tool_names

    def test_malformed_upstream_env_var_returns_empty_and_logs_warning(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        with caplog.at_level(logging.WARNING):
            result = load_upstream_mcp_servers("not valid json at all")

        assert result == ()
        assert any("invalid JSON" in record.message for record in caplog.records)

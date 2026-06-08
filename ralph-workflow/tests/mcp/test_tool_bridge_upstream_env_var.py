"""Tests for ralph.mcp.tool_bridge — T12 extensibility tests."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.mcp.tools.names import (
    ALL_RALPH_TOOLS,
)
from ralph.mcp.upstream.config import (
    UpstreamMcpServer,
    load_upstream_mcp_servers,
    serialize_upstream_mcp_servers,
)
from ralph.mcp.upstream.registry import UpstreamRegistry

if TYPE_CHECKING:
    import pytest
from tests.mcp.test_tool_bridge_upstream_env_var_helper__allowedsession import _AllowedSession
from tests.mcp.test_tool_bridge_upstream_env_var_helper__fakeupstreamclientfactory import (
    _FakeUpstreamClientFactory,
)
from tests.mcp.test_tool_bridge_upstream_env_var_helper__fakeworkspace import _FakeWorkspace


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

        fake_tools: list[dict[str, object]] = [
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
        upstream_reg = UpstreamRegistry.build([fake_upstream_server], client_factory=mock_factory)

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
        bridge.set_client_capabilities({"image", "media"})

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

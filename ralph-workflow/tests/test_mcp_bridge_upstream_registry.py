"""Tests for ralph/mcp/artifacts/bridge.py — MCP bridge layer."""

from __future__ import annotations

import pytest

from ralph.mcp.upstream.client import HttpUpstreamClient
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.models import UpstreamCallError
from ralph.mcp.upstream.registry import RegistryCollisionError, UpstreamRegistry

METHOD_NOT_FOUND_CODE = -32601
INVALID_REQUEST_CODE = -32600


def _object_dict(value: object) -> dict[str, object]:
    assert isinstance(value, dict)
    return value


def _object_list(value: object) -> list[object]:
    assert isinstance(value, list)
    return value


class TestUpstreamRegistry:
    def _make_tools_caller(self, tools: list[dict[str, object]]) -> object:
        def caller(method: str, params: dict[str, object]) -> dict[str, object]:
            if method == "tools/list":
                return {"tools": tools}
            return {}

        return caller

    def test_custom_mcp_registry_uses_custom_namespace(self) -> None:
        custom_server = UpstreamMcpServer(
            name="filesystem",
            transport="http",
            url="http://unused",
            origin="custom",
        )

        custom_caller = self._make_tools_caller(
            [{"name": "read_file", "description": "Read a file", "inputSchema": {}}]
        )

        registry = UpstreamRegistry.build(
            [custom_server],
            client_factory=lambda server: HttpUpstreamClient(server, caller=custom_caller),
        )
        aliases = {t.alias for t in registry.tool_definitions()}

        assert "ralph_custom__filesystem__read_file" in aliases
        assert "ralph_upstream__filesystem__read_file" not in aliases

    def test_upstream_registry_namespaces_tools_by_server(self) -> None:
        fs_server = UpstreamMcpServer(name="filesystem", transport="http", url="http://unused")
        gh_server = UpstreamMcpServer(name="github", transport="http", url="http://unused")

        fs_caller = self._make_tools_caller(
            [{"name": "read_file", "description": "Read a file", "inputSchema": {}}]
        )
        gh_caller = self._make_tools_caller(
            [{"name": "search_repos", "description": "Search GitHub repos", "inputSchema": {}}]
        )

        def client_factory(server: UpstreamMcpServer) -> HttpUpstreamClient:
            if server.name == "filesystem":
                return HttpUpstreamClient(server, caller=fs_caller)
            return HttpUpstreamClient(server, caller=gh_caller)

        registry = UpstreamRegistry.build(
            [fs_server, gh_server],
            client_factory=client_factory,
        )
        aliases = {t.alias for t in registry.tool_definitions()}

        assert "ralph_upstream__filesystem__read_file" in aliases
        assert "ralph_upstream__github__search_repos" in aliases

    def test_upstream_registry_rejects_colliding_proxy_aliases(self) -> None:
        # Server "a__b" tool "c" → ralph_upstream__a__b__c
        # Server "a"    tool "b__c" → ralph_upstream__a__b__c  ← same alias, collision
        server_producing_ralph_upstream__a__b__c_via_server = UpstreamMcpServer(
            name="a__b", transport="http", url="http://unused"
        )
        server_producing_ralph_upstream__a__b__c_via_tool = UpstreamMcpServer(
            name="a", transport="http", url="http://unused"
        )

        ab_caller = self._make_tools_caller([{"name": "c", "description": "", "inputSchema": {}}])
        a_caller = self._make_tools_caller([{"name": "b__c", "description": "", "inputSchema": {}}])

        def client_factory(server: UpstreamMcpServer) -> HttpUpstreamClient:
            if server.name == "a__b":
                return HttpUpstreamClient(server, caller=ab_caller)
            return HttpUpstreamClient(server, caller=a_caller)

        with pytest.raises(RegistryCollisionError, match="alias collision"):
            UpstreamRegistry.build(
                [
                    server_producing_ralph_upstream__a__b__c_via_server,
                    server_producing_ralph_upstream__a__b__c_via_tool,
                ],
                client_factory=client_factory,
            )

    def test_upstream_registry_skips_unhealthy_server(self) -> None:
        healthy = UpstreamMcpServer(name="good", transport="http", url="http://unused")
        unhealthy = UpstreamMcpServer(name="bad", transport="http", url="http://unused")

        good_caller = self._make_tools_caller(
            [{"name": "do_thing", "description": "Does a thing", "inputSchema": {}}]
        )

        def failing_caller(method: str, params: dict[str, object]) -> dict[str, object]:
            raise UpstreamCallError("connection refused")

        def client_factory(server: UpstreamMcpServer) -> HttpUpstreamClient:
            if server.name == "good":
                return HttpUpstreamClient(server, caller=good_caller)
            return HttpUpstreamClient(server, caller=failing_caller)

        registry = UpstreamRegistry.build(
            [healthy, unhealthy],
            client_factory=client_factory,
            on_unreachable="warn_and_skip",
        )
        aliases = {t.alias for t in registry.tool_definitions()}

        assert "ralph_upstream__good__do_thing" in aliases
        assert not any("bad" in alias for alias in aliases)

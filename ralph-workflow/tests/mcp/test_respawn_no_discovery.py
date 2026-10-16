"""R2 invariant: a respawned child does zero network I/O before serving."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from ralph.mcp.upstream.client import HttpUpstreamClient
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.models import UpstreamTool
from ralph.mcp.upstream.registry import UpstreamRegistry

pytestmark = pytest.mark.subprocess_e2e


class _CountingUpstreamClient(HttpUpstreamClient):
    """HttpUpstreamClient subclass that records list_tools calls without network I/O."""

    def __init__(self, server: UpstreamMcpServer, counter: list[int]) -> None:
        # Bypass HttpUpstreamClient.__init__ (which calls _make_http_caller on
        # server.url) by deferring to object.__init__ and storing server only.
        object.__init__(self)
        self._server = server
        self._counter = counter

    def list_tools(self) -> list[UpstreamTool]:
        self._counter.append(1)
        return [
            UpstreamTool(
                name=f"tool-{self._server.name}",
                description="fake",
                input_schema={"type": "object"},
            )
        ]

    def call_tool(self, name: str, arguments: dict[str, object]) -> dict[str, object]:
        return {}


def _make_counting_factory(
    counter: list[int],
) -> Callable[[UpstreamMcpServer], HttpUpstreamClient]:
    def factory(server: UpstreamMcpServer) -> HttpUpstreamClient:
        return _CountingUpstreamClient(server, counter)

    return factory


def test_respawn_with_cached_catalog_makes_zero_discovery_calls() -> None:
    counter: list[int] = []
    factory = _make_counting_factory(counter)
    server = UpstreamMcpServer(
        name="remote", transport="http", url="http://unused.example/mcp"
    )
    initial = UpstreamRegistry.build([server], client_factory=factory)
    initial_count = len(counter)
    assert initial_count == 1
    catalog_for_respawn = {server.name: [pt.tool for pt in initial.tool_definitions()]}
    respawn = UpstreamRegistry.build_from_tool_catalog(
        [server], catalog_for_respawn, client_factory=factory
    )
    assert len(counter) == initial_count
    assert len(respawn.tool_definitions()) == len(initial.tool_definitions())


def test_respawn_factory_never_called_for_cached_servers() -> None:
    counter: list[int] = []
    factory = _make_counting_factory(counter)
    server = UpstreamMcpServer(
        name="remote", transport="http", url="http://unused.example/mcp"
    )
    catalog = {
        server.name: [
            UpstreamTool(name="alpha", description="seeded", input_schema={"type": "object"})
        ]
    }
    UpstreamRegistry.build_from_tool_catalog([server], catalog, client_factory=factory)
    assert counter == []


@pytest.mark.timeout_seconds(5)
def test_respawn_via_tool_catalog_serves_one_tool() -> None:
    server = UpstreamMcpServer(
        name="remote", transport="http", url="http://unused.example/mcp"
    )
    catalog = {
        server.name: [
            UpstreamTool(name="alpha", description="seeded", input_schema={"type": "object"})
        ]
    }
    respawn = UpstreamRegistry.build_from_tool_catalog([server], catalog)
    tools = respawn.tool_definitions()
    assert len(tools) == 1
    assert tools[0].alias.endswith("__alpha")

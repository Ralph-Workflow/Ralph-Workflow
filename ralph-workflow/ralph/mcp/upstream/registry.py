"""Registry that aggregates tools from multiple upstream MCP servers.

``UpstreamRegistry`` is built from a list of configured ``UpstreamMcpServer`` entries;
it contacts each server, collects its tool list, assigns stable alias names via
``upstream_proxy_tool_name``, and exposes ``tool_definitions`` and ``call_tool`` for
use by the MCP bridge. Alias collisions raise ``RegistryCollisionError`` immediately.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from loguru import logger

from ralph.mcp.tools.names import upstream_proxy_tool_name
from ralph.mcp.upstream.client import (
    HasMediaManifest,
    HttpUpstreamClient,
    JsonObject,
    StdioUpstreamClient,
    make_upstream_client,
    normalize_upstream_content_blocks,
)
from ralph.mcp.upstream.models import UpstreamCallError, UpstreamTool

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ralph.mcp.upstream.config import UpstreamMcpServer

_AnyUpstreamClient = HttpUpstreamClient | StdioUpstreamClient

if TYPE_CHECKING:
    UpstreamClientFactory = Callable[[UpstreamMcpServer], _AnyUpstreamClient]


class RegistryCollisionError(ValueError):
    pass


@dataclass(frozen=True)
class ProxiedTool:
    alias: str
    server_name: str
    tool: UpstreamTool


class UpstreamRegistry:
    def __init__(
        self,
        proxied_tools: list[ProxiedTool],
        clients: dict[str, _AnyUpstreamClient],
    ) -> None:
        self._proxied_tools = proxied_tools
        self._clients = clients
        self._alias_map: dict[str, ProxiedTool] = {t.alias: t for t in proxied_tools}

    @classmethod
    def build(
        cls,
        servers: Iterable[UpstreamMcpServer],
        *,
        client_factory: UpstreamClientFactory | None = None,
        on_unreachable: Literal["raise", "warn_and_skip"] = "raise",
    ) -> UpstreamRegistry:
        from ralph.mcp.upstream.validation import UpstreamValidationError  # noqa: PLC0415

        _factory = client_factory if client_factory is not None else make_upstream_client
        seen_aliases: dict[str, tuple[str, str]] = {}
        proxied_tools: list[ProxiedTool] = []
        clients: dict[str, _AnyUpstreamClient] = {}

        for server in servers:
            client = _factory(server)
            try:
                tools = client.list_tools()
            except UpstreamCallError as exc:
                if on_unreachable == "raise":
                    env_key_repr = f" env_keys={sorted(server.env.keys())}" if server.env else ""
                    raise UpstreamValidationError(
                        f"upstream MCP server '{server.name}'{env_key_repr} is unreachable: {exc}"
                    ) from exc
                logger.warning("Skipping upstream MCP server {}: {}", server.name, exc)
                continue

            clients[server.name] = client
            for tool in tools:
                alias = upstream_proxy_tool_name(server.name, tool.name)
                if alias in seen_aliases:
                    prev_server, prev_tool = seen_aliases[alias]
                    raise RegistryCollisionError(
                        f"alias collision: '{alias}' produced by "
                        f"({server.name!r}, {tool.name!r}) conflicts with "
                        f"({prev_server!r}, {prev_tool!r})"
                    )
                seen_aliases[alias] = (server.name, tool.name)
                proxied_tools.append(ProxiedTool(alias=alias, server_name=server.name, tool=tool))

        return cls(proxied_tools, clients)

    def tool_definitions(self) -> list[ProxiedTool]:
        return list(self._proxied_tools)

    def call_tool(
        self,
        alias: str,
        arguments: JsonObject,
        session: HasMediaManifest | None = None,
    ) -> object:
        if alias not in self._alias_map:
            raise UpstreamCallError(f"proxied tool '{alias}' not found in upstream registry")
        proxied = self._alias_map[alias]
        client = self._clients[proxied.server_name]
        raw_result = client.call_tool(proxied.tool.name, arguments)
        if isinstance(raw_result, dict):
            result: JsonObject = raw_result
            normalize_upstream_content_blocks(
                result, proxied.server_name, proxied.tool.name, session
            )
            return result
        return raw_result


__all__ = [
    "ProxiedTool",
    "RegistryCollisionError",
    "UpstreamRegistry",
]

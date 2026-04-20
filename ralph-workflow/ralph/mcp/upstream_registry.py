from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from loguru import logger

from ralph.mcp.tool_names import upstream_proxy_tool_name
from ralph.mcp.upstream_client import (
    HttpUpstreamClient,
    StdioUpstreamClient,
    make_upstream_client,
)
from ralph.mcp.upstream_models import UpstreamCallError, UpstreamTool

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable

    from ralph.mcp.upstream_client import JsonObject
    from ralph.mcp.upstream_config import UpstreamMcpServer

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
        from ralph.mcp.upstream_validation import UpstreamValidationError  # noqa: PLC0415

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

    def call_tool(self, alias: str, arguments: JsonObject) -> object:
        if alias not in self._alias_map:
            raise UpstreamCallError(f"proxied tool '{alias}' not found in upstream registry")
        proxied = self._alias_map[alias]
        client = self._clients[proxied.server_name]
        return client.call_tool(proxied.tool.name, arguments)


__all__ = [
    "ProxiedTool",
    "RegistryCollisionError",
    "UpstreamRegistry",
]

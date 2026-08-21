"""Registry that aggregates tools from multiple upstream MCP servers.

``UpstreamRegistry`` is built from a list of configured ``UpstreamMcpServer`` entries;
it contacts each server, collects its tool list, assigns stable alias names via
``upstream_proxy_tool_name``, and exposes ``tool_definitions`` and ``call_tool`` for
use by the MCP bridge. Alias collisions raise ``RegistryCollisionError`` immediately.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Literal, cast

from loguru import logger

from ralph.mcp.tools.names import proxied_mcp_tool_name
from ralph.mcp.upstream._proxied_tool import ProxiedTool
from ralph.mcp.upstream._registry_collision_error import RegistryCollisionError
from ralph.mcp.upstream.client import (
    HasMediaManifest,
    HttpUpstreamClient,
    JsonObject,
    StdioUpstreamClient,
    make_upstream_client,
    normalize_upstream_content_blocks,
)
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.upstream.models import UpstreamCallError, UpstreamTool
from ralph.mcp.upstream.validation import UpstreamValidationError

if TYPE_CHECKING:
    from ralph.workspace.protocol import Workspace

_AnyUpstreamClient = HttpUpstreamClient | StdioUpstreamClient
UpstreamClientFactory = Callable[[UpstreamMcpServer], _AnyUpstreamClient]


class UpstreamRegistry:
    """Aggregates tools from multiple upstream MCP servers under stable proxy aliases."""

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
        _factory = client_factory if client_factory is not None else make_upstream_client
        seen_aliases: dict[str, tuple[str, str]] = {}
        proxied_tools: list[ProxiedTool] = []
        clients: dict[str, _AnyUpstreamClient] = {}

        for server in servers:
            client = _factory(server)
            server_kind = (
                "custom MCP server" if server.origin == "custom" else "upstream MCP server"
            )
            try:
                tools = client.list_tools()
            except UpstreamCallError as exc:
                if on_unreachable == "raise":
                    env_key_repr = f" env_keys={sorted(server.env.keys())}" if server.env else ""
                    raise UpstreamValidationError(
                        f"{server_kind} '{server.name}'{env_key_repr} is unreachable: {exc}"
                    ) from exc
                logger.warning("Skipping {} {}: {}", server_kind, server.name, exc)
                continue

            clients[server.name] = client
            for tool in tools:
                alias = proxied_mcp_tool_name(server.name, tool.name, origin=server.origin)
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

    @classmethod
    def build_from_tool_catalog(
        cls,
        servers: Iterable[UpstreamMcpServer],
        tool_catalog: dict[str, list[UpstreamTool]],
        *,
        client_factory: UpstreamClientFactory | None = None,
    ) -> UpstreamRegistry:
        """Build a registry from pre-discovered tools without probing upstreams."""

        _factory = client_factory if client_factory is not None else make_upstream_client
        seen_aliases: dict[str, tuple[str, str]] = {}
        proxied_tools: list[ProxiedTool] = []
        clients: dict[str, _AnyUpstreamClient] = {}

        for server in servers:
            tools = tool_catalog.get(server.name)
            if not tools:
                continue
            clients[server.name] = _factory(server)
            for tool in tools:
                alias = proxied_mcp_tool_name(server.name, tool.name, origin=server.origin)
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
        workspace: Workspace | None = None,
    ) -> object:
        if alias not in self._alias_map:
            raise UpstreamCallError(f"proxied tool '{alias}' not found in upstream registry")
        proxied = self._alias_map[alias]
        client = self._clients[proxied.server_name]
        raw_result = client.call_tool(proxied.tool.name, arguments)
        return self._normalized_result(raw_result, proxied, session, workspace)

    def _normalized_result(
        self,
        raw_result: object,
        proxied: ProxiedTool,
        session: HasMediaManifest | None,
        workspace: Workspace | None,
    ) -> object:
        """Normalise an upstream result WHATEVER shape it arrives in.

        Normalising only a ``dict`` left two shapes untouched, and both
        reach the agent verbatim: a BARE LIST of content blocks, and a
        result nested under ``{"result": {...}}`` -- which the server
        layer unwraps AFTER this runs, so normalisation had inspected an
        outer dict with no ``content`` key and found nothing to do.

        Either one delivered a raw base64 ``image`` block to a codex
        caller: the exact wire shape of the incident this contract
        exists to prevent, arriving by the one route that skipped the
        contract. An upstream server has to be non-conforming to produce
        them, which is precisely when a guard has to hold.
        """
        if isinstance(raw_result, list):
            blocks: list[object] = cast("list[object]", raw_result)  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
            wrapper: JsonObject = {"content": blocks}
            normalize_upstream_content_blocks(
                wrapper, proxied.server_name, proxied.tool.name, session, workspace
            )
            normalised: object = wrapper["content"]
            return normalised
        if not isinstance(raw_result, dict):
            return raw_result
        result: JsonObject = raw_result
        # Unwrapped BEFORE normalising, in the same order the server
        # layer reads it back out.
        nested: object = result.get("result")
        if isinstance(nested, dict) and "content" not in result:
            normalize_upstream_content_blocks(
                nested, proxied.server_name, proxied.tool.name, session, workspace
            )
        normalize_upstream_content_blocks(
            result, proxied.server_name, proxied.tool.name, session, workspace
        )
        return result


__all__ = [
    "ProxiedTool",
    "RegistryCollisionError",
    "UpstreamRegistry",
]

"""Registry that aggregates tools from multiple upstream MCP servers.

``UpstreamRegistry`` is built from a list of configured ``UpstreamMcpServer`` entries;
it contacts each server, collects its tool list, assigns stable alias names via
``upstream_proxy_tool_name``, and exposes ``tool_definitions`` and ``call_tool`` for
use by the MCP bridge. Alias collisions raise ``RegistryCollisionError`` immediately.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Literal, cast

from loguru import logger

from ralph.mcp.protocol._startup_timeouts import mcp_discovery_budget_from_env
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
    from collections.abc import Sequence

    from ralph.workspace.protocol import Workspace

_AnyUpstreamClient = HttpUpstreamClient | StdioUpstreamClient
UpstreamClientFactory = Callable[[UpstreamMcpServer], _AnyUpstreamClient]


def _log_probe_outcome(
    server_name: str,
    server_kind: str,
    elapsed_ms: float,
    outcome: str,
    reason: str,
) -> None:
    """Log a structured probe outcome line (no env-value or secret leakage)."""
    msg = (
        f"ralph-mcp upstream probe: server={server_name} "
        f"elapsed_ms={elapsed_ms:.1f} outcome={outcome} reason={reason}"
    )
    if outcome == "strict_failure":
        logger.error(msg)
    else:
        logger.warning(msg)


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
        _monotonic: Callable[[], float] | None = None,
    ) -> UpstreamRegistry:
        """Build a registry by probing each server for its tool list.

        Args:
            servers: iterable of configured upstream servers.
            client_factory: optional factory for creating upstream clients.
            on_unreachable: when to raise vs. warn-and-skip.
            _monotonic: injectable clock for testing (defaults to time.monotonic).

        The aggregate first-start discovery budget is ``mcp_discovery_budget_from_env``,
        which is strictly below the preflight timeout by construction. When the
        deadline expires, remaining servers are warned-and-skipped rather than
        probed, guaranteeing the worst-case discovery time is bounded below the
        preflight timeout.
        """

        _factory = client_factory if client_factory is not None else make_upstream_client
        _clock = _monotonic if _monotonic is not None else time.monotonic

        discovery_budget_seconds = mcp_discovery_budget_from_env().total_seconds()
        deadline = _clock() + discovery_budget_seconds

        seen_aliases: dict[str, tuple[str, str]] = {}
        proxied_tools: list[ProxiedTool] = []
        clients: dict[str, _AnyUpstreamClient] = {}

        for server in servers:
            # Check aggregate budget deadline before probing each server.
            remaining = deadline - _clock()
            if remaining <= 0:
                # R2 strict-fail-closed: an exhausted aggregate discovery
                # budget must NOT silently return an incomplete registry
                # when the caller asked us to fail closed. Warn-and-skip
                # is the right default for the agent-upstream branch
                # (those servers are best-effort and owned by the agent
                # runtime), but ``on_unreachable="raise"`` -- which the
                # runtime passes whenever ``RALPH_MCP_STRICT`` is on --
                # is the contract that the operator expected a strict
                # first start to enforce. The same probe that would
                # raise ``UpstreamValidationError`` for an unreachable
                # server must raise it here too: the registry that
                # ``build`` returns is partial, and a partial registry
                # is a runtime hazard (a child that binds a tool it
                # cannot serve when probed downstream).
                server_kind = (
                    "custom MCP server" if server.origin == "custom" else "upstream MCP server"
                )
                if on_unreachable == "raise":
                    _log_probe_outcome(
                        server.name,
                        server_kind,
                        elapsed_ms=0.0,
                        outcome="strict_failure",
                        reason="budget_exhausted",
                    )
                    raise UpstreamValidationError(
                        f"{server_kind} '{server.name}' is unreachable: aggregate "
                        f"discovery budget ({discovery_budget_seconds:.1f}s) "
                        f"exhausted before reaching this server."
                    )
                _log_probe_outcome(
                    server.name,
                    server_kind,
                    elapsed_ms=0.0,
                    outcome="skipped",
                    reason="budget_exhausted",
                )
                logger.warning(
                    "Skipping {} {}: aggregate discovery budget ({:.1f}s) exhausted "
                    "before reaching this server.",
                    server_kind,
                    server.name,
                    discovery_budget_seconds,
                )
                continue

            client = _factory(server)
            server_kind = (
                "custom MCP server" if server.origin == "custom" else "upstream MCP server"
            )
            try:
                # D-04: per-server probe timing so the operator sees
                # which upstream consumed the discovery budget.
                probe_start = _clock()
                tools = client.list_tools(timeout_s=remaining)
            except UpstreamCallError as exc:
                probe_elapsed_ms = (_clock() - probe_start) * 1000.0
                exc_class_name = type(exc).__name__
                if on_unreachable == "raise":
                    _log_probe_outcome(
                        server.name,
                        server_kind,
                        probe_elapsed_ms,
                        "strict_failure",
                        exc_class_name,
                    )
                    env_key_repr = f" env_keys={sorted(server.env.keys())}" if server.env else ""
                    raise UpstreamValidationError(
                        f"{server_kind} '{server.name}'{env_key_repr} is unreachable: {exc}"
                    ) from exc
                _log_probe_outcome(
                    server.name,
                    server_kind,
                    probe_elapsed_ms,
                    "skipped",
                    exc_class_name,
                )
                continue
            else:
                probe_elapsed_ms = (_clock() - probe_start) * 1000.0
                if _clock() > deadline:
                    budget_exc = UpstreamCallError(
                        f"upstream server '{server.name}' exceeded discovery budget deadline"
                    )
                    if on_unreachable == "raise":
                        _log_probe_outcome(
                            server.name,
                            server_kind,
                            probe_elapsed_ms,
                            "strict_failure",
                            "UpstreamCallError",
                        )
                        raise UpstreamValidationError(
                            f"{server_kind} '{server.name}' is unreachable: {budget_exc}"
                        ) from budget_exc
                    _log_probe_outcome(
                        server.name,
                        server_kind,
                        probe_elapsed_ms,
                        "skipped",
                        "UpstreamCallError",
                    )
                    continue
                logger.info(
                    "ralph-mcp upstream probe: server={name} elapsed_ms={ms:.1f} n_tools={n}",
                    name=server.name,
                    ms=probe_elapsed_ms,
                    n=len(tools),
                )

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

        Normalising only a ``dict`` left other shapes untouched, and
        they reach the agent verbatim: a bare LIST or TUPLE of content
        blocks (the server layer accepts either), and a result nested
        under ``{"result": {...}}`` -- which the server
        layer unwraps AFTER this runs, so normalisation had inspected an
        outer dict with no ``content`` key and found nothing to do.

        Either one delivered a raw base64 ``image`` block to a codex
        caller: the exact wire shape of the incident this contract
        exists to prevent, arriving by the one route that skipped the
        contract. An upstream server has to be non-conforming to produce
        them, which is precisely when a guard has to hold.
        """
        if isinstance(raw_result, (list, tuple)):
            blocks: list[object] = list(
                cast("Sequence[object]", raw_result)
            )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
            wrapper: JsonObject = {"content": blocks}
            normalize_upstream_content_blocks(
                wrapper, proxied.server_name, proxied.tool.name, session, workspace
            )
            normalised: object = wrapper["content"]
            return normalised
        if not isinstance(raw_result, dict):
            return raw_result
        result: JsonObject = raw_result
        # Normalised UNCONDITIONALLY when present. The previous guard
        # was ``and "content" not in result``, the exact OPPOSITE of the
        # server layer's rule: ``_build_tools_call_payload`` unwraps
        # ``result`` whenever it is a dict and DISCARDS the outer
        # content. So a payload carrying both -- an outer text block
        # beside a nested image -- was declined here and served there,
        # putting raw base64 image bytes in front of a codex agent by
        # the one route that skips this contract. The comment claimed
        # the two orders matched; they did not.
        nested: object = result.get("result")
        if isinstance(nested, dict):
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

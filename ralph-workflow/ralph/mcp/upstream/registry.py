from __future__ import annotations

import concurrent.futures
import time
from collections.abc import Callable, Iterable
from typing import TYPE_CHECKING, Literal

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
    from ralph.workspace.protocol import Workspace

_AnyUpstreamClient = HttpUpstreamClient | StdioUpstreamClient
UpstreamClientFactory = Callable[[UpstreamMcpServer], _AnyUpstreamClient]

_MAX_PROBE_WORKERS: int = 8
_MAX_PROBE_WORKERS_MAX: int = 32
if not (1 <= _MAX_PROBE_WORKERS <= _MAX_PROBE_WORKERS_MAX):
    raise RuntimeError(
        f"_MAX_PROBE_WORKERS must be in [1, {_MAX_PROBE_WORKERS_MAX}], got {_MAX_PROBE_WORKERS}"
    )


class _BudgetExhaustedError(Exception):
    __slots__ = ("server",)

    def __init__(self, server: UpstreamMcpServer) -> None:
        super().__init__(f"upstream server '{server.name}' budget exhausted before probe")
        self.server = server


def _log_probe_outcome(
    server_name: str,
    server_kind: str,
    elapsed_ms: float,
    outcome: str,
    reason: str,
) -> None:
    msg = (
        f"ralph-mcp upstream probe: server={server_name} "
        f"elapsed_ms={elapsed_ms:.1f} outcome={outcome} reason={reason}"
    )
    if outcome == "strict_failure":
        logger.error(msg)
    else:
        logger.warning(msg)


def _register_tools(
    server: UpstreamMcpServer,
    tools: list[UpstreamTool] | None,
    seen_aliases: dict[str, tuple[str, str]],
    proxied_tools: list[ProxiedTool],
) -> None:
    for tool in tools or []:
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


def _handle_completed_probe(
    server: UpstreamMcpServer,
    server_kind: str,
    client: _AnyUpstreamClient | None,
    tools: list[UpstreamTool] | None,
    error: BaseException | None,
    probe_elapsed_ms: float,
    *,
    deadline: float,
    clock: Callable[[], float],
    discovery_budget_seconds: float,
    on_unreachable: Literal["raise", "warn_and_skip"],
    seen_aliases: dict[str, tuple[str, str]],
    proxied_tools: list[ProxiedTool],
    clients: dict[str, _AnyUpstreamClient],
    logged_servers: set[str],
) -> None:
    logged_servers.add(server.name)
    if not isinstance(error, _BudgetExhaustedError) and clock() > deadline:
        _log_probe_outcome(
            server.name,
            server_kind,
            probe_elapsed_ms,
            "skipped",
            "budget_exhausted",
        )
        return

    if isinstance(error, _BudgetExhaustedError):
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
        return

    if error is not None:
        exc_class_name = type(error).__name__
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
                f"{server_kind} '{server.name}'{env_key_repr} is unreachable: {error}"
            ) from error
        _log_probe_outcome(
            server.name,
            server_kind,
            probe_elapsed_ms,
            "skipped",
            exc_class_name,
        )
        return

    logger.info(
        "ralph-mcp upstream probe: server={name} elapsed_ms={ms:.1f} n_tools={n}",
        name=server.name,
        ms=probe_elapsed_ms,
        n=len(tools) if tools is not None else 0,
    )
    if client is not None:
        clients[server.name] = client
    _register_tools(server, tools, seen_aliases, proxied_tools)


def _check_strict_completeness(
    servers_list: list[UpstreamMcpServer],
    clients: dict[str, _AnyUpstreamClient],
    discovery_budget_seconds: float,
) -> None:
    if len(clients) < len(servers_list):
        for server in servers_list:
            if server.name not in clients:
                server_kind = (
                    "custom MCP server" if server.origin == "custom" else "upstream MCP server"
                )
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


def _probe_upstream_server(
    server: UpstreamMcpServer,
    deadline: float,
    factory: UpstreamClientFactory,
    clock: Callable[[], float],
) -> tuple[
    UpstreamMcpServer,
    str,
    _AnyUpstreamClient | None,
    list[UpstreamTool] | None,
    BaseException | _BudgetExhaustedError | None,
    float,
]:
    server_kind = "custom MCP server" if server.origin == "custom" else "upstream MCP server"
    timeout_s = max(0.0, deadline - clock())
    if timeout_s <= 0:
        return (server, server_kind, None, None, _BudgetExhaustedError(server), 0.0)
    try:
        probe_start = clock()
        client = factory(server)
        timeout_s = max(0.0, deadline - clock())
        if timeout_s <= 0:
            return (server, server_kind, None, None, _BudgetExhaustedError(server), 0.0)
        tools = client.list_tools(timeout_s=timeout_s)
        elapsed_ms = (clock() - probe_start) * 1000.0
    except UpstreamCallError as exc:
        return (server, server_kind, None, None, exc, (clock() - probe_start) * 1000.0)
    return (server, server_kind, client, tools, None, elapsed_ms)


def _log_unreached_servers_on_expiry(
    servers_list: list[UpstreamMcpServer],
    clients: dict[str, _AnyUpstreamClient],
    logged_servers: set[str],
    discovery_budget_seconds: float,
) -> None:
    for server in servers_list:
        if server.name not in clients and server.name not in logged_servers:
            server_kind = (
                "custom MCP server" if server.origin == "custom" else "upstream MCP server"
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
        _monotonic: Callable[[], float] | None = None,
    ) -> UpstreamRegistry:

        _factory = client_factory if client_factory is not None else make_upstream_client
        _clock = _monotonic if _monotonic is not None else time.monotonic

        servers_list = list(servers)
        if len(servers_list) == 0:
            return cls([], {})

        discovery_budget_seconds = mcp_discovery_budget_from_env().total_seconds()
        deadline = _clock() + discovery_budget_seconds

        seen_aliases: dict[str, tuple[str, str]] = {}
        proxied_tools: list[ProxiedTool] = []
        clients: dict[str, _AnyUpstreamClient] = {}
        logged_servers: set[str] = set()

        max_workers = min(_MAX_PROBE_WORKERS, len(servers_list))
        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
        future_to_server: dict[
            concurrent.futures.Future[
                tuple[
                    UpstreamMcpServer,
                    str,
                    _AnyUpstreamClient | None,
                    list[UpstreamTool] | None,
                    BaseException | _BudgetExhaustedError | None,
                    float,
                ]
            ],
            UpstreamMcpServer,
        ] = {
            executor.submit(_probe_upstream_server, server, deadline, _factory, _clock): server
            for server in servers_list
        }
        pending: set[
            concurrent.futures.Future[
                tuple[
                    UpstreamMcpServer,
                    str,
                    _AnyUpstreamClient | None,
                    list[UpstreamTool] | None,
                    BaseException | _BudgetExhaustedError | None,
                    float,
                ]
            ]
        ] = set(future_to_server.keys())
        collector_expired = False

        try:
            while pending:
                remaining = max(0.0, deadline - _clock())
                drain_timeout = (
                    remaining
                    if remaining > 0.0
                    else (0.0 if any(f.done() for f in pending) else 0.05)
                )
                try:
                    future = next(concurrent.futures.as_completed(pending, timeout=drain_timeout))
                except StopIteration:
                    break
                except concurrent.futures.TimeoutError:
                    collector_expired = True
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                pending.discard(future)
                try:
                    server, server_kind, client, tools, error, probe_elapsed_ms = future.result()
                except Exception as exc:
                    error = exc
                    client = None
                    tools = None
                    server = future_to_server[future]
                    server_kind = (
                        "custom MCP server" if server.origin == "custom" else "upstream MCP server"
                    )
                    probe_elapsed_ms = 0.0

                _handle_completed_probe(
                    server,
                    server_kind,
                    client,
                    tools,
                    error,
                    probe_elapsed_ms,
                    deadline=deadline,
                    clock=_clock,
                    discovery_budget_seconds=discovery_budget_seconds,
                    on_unreachable=on_unreachable,
                    seen_aliases=seen_aliases,
                    proxied_tools=proxied_tools,
                    clients=clients,
                    logged_servers=logged_servers,
                )
        finally:
            executor.shutdown(wait=False, cancel_futures=True)

        if collector_expired:
            logger.warning("ralph-mcp discovery collector expired before all upstreams completed")
            if on_unreachable == "warn_and_skip":
                _log_unreached_servers_on_expiry(
                    servers_list, clients, logged_servers, discovery_budget_seconds
                )

        if on_unreachable == "raise":
            _check_strict_completeness(servers_list, clients, discovery_budget_seconds)

        return cls(proxied_tools, clients)

    @classmethod
    def build_from_tool_catalog(
        cls,
        servers: Iterable[UpstreamMcpServer],
        tool_catalog: dict[str, list[UpstreamTool]],
        *,
        client_factory: UpstreamClientFactory | None = None,
    ) -> UpstreamRegistry:

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
        if isinstance(raw_result, (list, tuple)):
            blocks: list[object] = list(raw_result)
            wrapper: JsonObject = {"content": blocks}
            normalize_upstream_content_blocks(
                wrapper, proxied.server_name, proxied.tool.name, session, workspace
            )
            normalised: object = wrapper["content"]
            return normalised
        if not isinstance(raw_result, dict):
            return raw_result
        result: JsonObject = raw_result
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

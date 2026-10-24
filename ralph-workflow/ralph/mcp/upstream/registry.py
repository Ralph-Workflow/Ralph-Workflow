"""Registry that aggregates tools from multiple upstream MCP servers.

``UpstreamRegistry`` is built from a list of configured ``UpstreamMcpServer`` entries;
it contacts each server, collects its tool list, assigns stable alias names via
``upstream_proxy_tool_name``, and exposes ``tool_definitions`` and ``call_tool`` for
use by the MCP bridge. Alias collisions raise ``RegistryCollisionError`` immediately.
"""

from __future__ import annotations

import concurrent.futures
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

#: Maximum number of probe workers the registry fans out to. Bounded so a
#: pathological config (hundreds of upstream servers) cannot spawn an
#: unbounded number of threads during first-start discovery. The per-call
#: pool size is ``min(_MAX_PROBE_WORKERS, len(servers_list))`` so small
#: server counts do not over-allocate threads. The bound is checked at
#: import time via ``if``/``raise`` (not ``assert``) so it survives
#: ``python -O``.
_MAX_PROBE_WORKERS: int = 8
#: Inclusive upper bound for the worker pool. The number is the same as
#: Python's default ``concurrent.futures.ThreadPoolExecutor`` ceiling
#: (``os.cpu_count() + 4`` capped at 32 on common systems).
_MAX_PROBE_WORKERS_MAX: int = 32
if not (1 <= _MAX_PROBE_WORKERS <= _MAX_PROBE_WORKERS_MAX):
    raise RuntimeError(
        f"_MAX_PROBE_WORKERS must be in [1, {_MAX_PROBE_WORKERS_MAX}], got {_MAX_PROBE_WORKERS}"
    )


class _BudgetExhaustedError(
    Exception
):  # name ends in ``Error`` per N818; private to the registry module
    """Per-worker sentinel: the aggregate deadline was already gone when this
    worker reached its budget check, so no probe was attempted.

    Subclasses ``Exception`` (not ``BaseException``) so the collector's
    ``try/except Exception`` defensive path treats it the same as a
    real probe error, and so the union type ``BaseException | Sentinel``
    in the worker's return type is well-formed. The collector
    distinguishes this from a real probe failure via
    ``isinstance(error, _BudgetExhaustedError)`` and routes it to
    the budget-exhausted logging branch (``reason=budget_exhausted``,
    the same reason the sequential code emitted for the pre-loop
    skip).
    """

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
    """Log a structured probe outcome line (no env-value or secret leakage)."""
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
    # Late-result rejection: genuine probe completed, but budget expired while in flight.
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

        Probes fan out concurrently on a bounded thread pool
        (``min(_MAX_PROBE_WORKERS, len(servers_list))`` workers) so a slow
        upstream does not pay its latency serially against every other
        configured server. The aggregate deadline is captured ONCE before
        the fan-out; each worker computes its own per-probe ``timeout_s``
        against that shared deadline at the moment of the call, and the
        collector recomputes its ``as_completed`` wait on every iteration
        so it cannot block past the deadline. Late results — those that
        land after the shared deadline — are dropped, preserving the
        existing late-result-rejection invariant. Under
        ``on_unreachable="raise"`` a deadline that elapses before every
        server is probed surfaces ``UpstreamValidationError`` (a partial
        registry is a runtime hazard, not a warning).
        """

        _factory = client_factory if client_factory is not None else make_upstream_client
        _clock = _monotonic if _monotonic is not None else time.monotonic

        # Materialize the iterable once: the empty-input fast path below
        # would otherwise have to consume ``servers`` twice, and the
        # fan-out itself needs an indexable sequence to submit. An empty
        # input MUST short-circuit BEFORE the executor is constructed so
        # the ``test_zero_servers_returns_empty_registry`` invariant
        # (the clock never advances) is preserved.
        servers_list = list(servers)
        if len(servers_list) == 0:
            return cls([], {})

        discovery_budget_seconds = mcp_discovery_budget_from_env().total_seconds()
        # Captured once: every worker and every collector iteration
        # computes its remaining budget against this same ``deadline``.
        # Re-evaluating ``mcp_discovery_budget_from_env()`` mid-build
        # would let a slow probe re-read the env and extend the budget
        # it is itself burning.
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
                if remaining <= 0.0 and not any(f.done() for f in pending):
                    collector_expired = True
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                try:
                    future = next(concurrent.futures.as_completed(pending, timeout=remaining))
                except StopIteration:
                    break
                except concurrent.futures.TimeoutError:
                    collector_expired = True
                    executor.shutdown(wait=False, cancel_futures=True)
                    break
                pending.discard(future)
                # Fetch the result FIRST: a worker that returned a
                # ``_BudgetExhaustedError`` is the canonical "the
                # pre-loop check would have skipped this server" signal
                # and must be processed even if the post-probe clock
                # has since advanced past the deadline. The
                # late-result rejection check (below) only applies to
                # genuine probe completions (errors or successful
                # tools).
                try:
                    server, server_kind, client, tools, error, probe_elapsed_ms = future.result()
                except Exception as exc:  # defensive: any future exception becomes a probe error
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
            # ``wait=False, cancel_futures=True`` so we never block the
            # build on a stuck worker after the deadline has elapsed:
            # the contract is bounded discovery time, not waiting for a
            # slow upstream that the deadline has already excluded.
            executor.shutdown(wait=False, cancel_futures=True)

        if collector_expired:
            logger.warning("ralph-mcp discovery collector expired before all upstreams completed")
            if on_unreachable == "warn_and_skip":
                _log_unreached_servers_on_expiry(
                    servers_list, clients, logged_servers, discovery_budget_seconds
                )

        # Strict-mode completeness: if any server was not reached within
        # budget (collector expiry, discarded late result, cancelled future,
        # or probe failure), raise UpstreamValidationError.
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

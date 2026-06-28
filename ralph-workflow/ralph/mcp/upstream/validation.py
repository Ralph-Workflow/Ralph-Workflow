"""Startup validation for user-defined upstream MCP servers.

Ralph fails fast if any custom MCP server cannot complete the standard
``initialize`` → ``notifications/initialized`` → ``tools/list`` handshake.
Set ``RALPH_MCP_STRICT=0`` to fall back to the legacy warn-and-skip
behaviour for CI smoke runs.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from loguru import logger

from ralph.mcp.protocol.startup import (
    PreflightError,
    mcp_preflight_timeout_from_env,
    preflight_http_mcp_server_tools,
)
from ralph.mcp.upstream._upstream_server_report import UpstreamServerReport
from ralph.mcp.upstream._upstream_validation_error import UpstreamValidationError
from ralph.mcp.upstream.client import make_upstream_client
from ralph.mcp.upstream.models import UpstreamCallError

if TYPE_CHECKING:
    from collections.abc import Callable, Iterable, Mapping
    from datetime import timedelta

    from ralph.mcp.upstream.config import UpstreamMcpServer

if TYPE_CHECKING:

    class HttpPreflightFn(Protocol):
        """Callable protocol for running an HTTP MCP server preflight check."""

        def __call__(
            self, endpoint: str, required_tools: tuple[str, ...], timeout: timedelta
        ) -> None: ...


_STRICT_ENV_VAR = "RALPH_MCP_STRICT"
_STRICT_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class UpstreamValidationReport:
    """Aggregated validation results for all configured upstream MCP servers."""

    servers: tuple[UpstreamServerReport, ...]

    @property
    def all_ok(self) -> bool:
        return all(s.ok for s in self.servers)

    @property
    def failures(self) -> tuple[UpstreamServerReport, ...]:
        return tuple(s for s in self.servers if not s.ok)


def strict_mode_from_env(env: Mapping[str, str] | None = None) -> bool:
    """Return True when strict mode is active (the default)."""

    env_map = os.environ if env is None else env
    raw = env_map.get(_STRICT_ENV_VAR)
    if raw is None:
        return True
    return raw.strip().lower() not in _STRICT_FALSE_VALUES


def _list_stdio_tools(server: UpstreamMcpServer, timeout: timedelta) -> list[str]:
    """Probe an stdio upstream by spawning the configured client.

    The ``timeout`` budget bounds the subprocess via :mod:`subprocess.run` so we
    never hang the orchestrator when an MCP binary forgets to flush stdout.

    Note: this is a bounded one-shot probe, not a long-lived tracked process.
    ProcessManager does not manage it because each probe runs to completion
    within the timeout; the underlying client cleans up its own subprocess.
    """

    del timeout  # subprocess timeout is enforced by the underlying client
    client = make_upstream_client(server)
    return [tool.name for tool in client.list_tools()]


def _redact_error(server: UpstreamMcpServer, exc: BaseException) -> str:
    """Render an exception message with upstream env values stripped out."""

    message = str(exc)
    for value in server.env.values():
        if value:
            message = message.replace(value, "***")
    return message


def _format_failure_report(failures: Iterable[UpstreamServerReport]) -> str:
    lines: list[str] = []
    for failure in failures:
        keys_part = f" env_keys={list(failure.secret_keys)}" if failure.secret_keys else ""
        lines.append(
            f"- {failure.name} (transport={failure.transport}){keys_part}: {failure.error}"
        )
    return "\n".join(lines)


def validate_upstream_mcp_servers(
    servers: Iterable[UpstreamMcpServer],
    *,
    timeout: timedelta | None = None,
    strict: bool | None = None,
    preflight_http: HttpPreflightFn = preflight_http_mcp_server_tools,
    list_stdio_tools: Callable[[UpstreamMcpServer, timedelta], list[str]] | None = None,
) -> UpstreamValidationReport:
    """Validate every configured upstream MCP server at startup.

    Args:
        servers: Iterable of normalized upstream MCP server definitions.
        timeout: Optional preflight timeout. Defaults to
            :func:`mcp_preflight_timeout_from_env` (30s, tunable via
            ``RALPH_MCP_PREFLIGHT_TIMEOUT_MS``).
        strict: Override strict-mode autodetection. If unset, reads
            ``RALPH_MCP_STRICT`` from the environment.
        preflight_http: Injection point for the HTTP preflight helper. Tests
            override this to drive the validator without touching the network.
        list_stdio_tools: Injection point for the stdio probe. Defaults to
            :func:`_list_stdio_tools`, which spawns the configured command
            through :class:`StdioUpstreamClient`.

    Returns:
        :class:`UpstreamValidationReport` with one entry per server. In soft
        mode failures are reported with ``ok=False`` and a warning is logged
        per failure. In strict mode an :class:`UpstreamValidationError` is
        raised after all servers are inspected so the diagnostic listing names
        every problem at once.
    """

    effective_timeout = timeout or mcp_preflight_timeout_from_env()
    effective_strict = strict_mode_from_env() if strict is None else strict
    effective_stdio_probe = list_stdio_tools or _list_stdio_tools

    reports: list[UpstreamServerReport] = []
    server_list = list(servers)
    for server in server_list:
        secret_keys = tuple(sorted(server.env.keys()))
        try:
            tool_count = _probe_one_server(
                server,
                effective_timeout,
                preflight_http=preflight_http,
                list_stdio_tools=effective_stdio_probe,
            )
        except (PreflightError, UpstreamCallError, ValueError, OSError) as exc:
            reports.append(
                UpstreamServerReport(
                    name=server.name,
                    transport=server.transport,
                    ok=False,
                    tool_count=0,
                    error=_redact_error(server, exc),
                    secret_keys=secret_keys,
                )
            )
            continue
        reports.append(
            UpstreamServerReport(
                name=server.name,
                transport=server.transport,
                ok=True,
                tool_count=tool_count,
                error=None,
                secret_keys=secret_keys,
            )
        )

    report = UpstreamValidationReport(servers=tuple(reports))
    failures = report.failures

    if not failures:
        if server_list:
            logger.info("Validated {} MCP server(s); all reachable.", len(server_list))
        return report

    # Servers imported from agent-native config (Claude `.mcp.json`, AGY, etc.) are
    # owned and launched by the agent runtime, not Ralph. They are best-effort: if a
    # third-party server cannot start, Ralph warns (so the operator can fix the agent's
    # MCP config) and continues without it — it never aborts the session. Only
    # Ralph-owned custom (`mcp.toml`) servers fail fast in strict mode.
    origin_by_name = {server.name: server.origin for server in server_list}
    agent_upstream_failures = tuple(f for f in failures if origin_by_name.get(f.name) != "custom")
    custom_failures = tuple(f for f in failures if origin_by_name.get(f.name) == "custom")

    for failure in agent_upstream_failures:
        logger.warning(
            "Agent-native MCP server '{}' ({}) failed startup validation and will be "
            "skipped; the agent runtime's MCP config is misconfigured: {}",
            failure.name,
            failure.transport,
            failure.error,
        )

    if effective_strict:
        if custom_failures:
            raise UpstreamValidationError(
                "Custom MCP servers failed startup validation:\n"
                + _format_failure_report(custom_failures)
            )
        return report

    for failure in custom_failures:
        logger.warning(
            "Custom MCP server '{}' ({}) failed validation: {}",
            failure.name,
            failure.transport,
            failure.error,
        )
    return report


def _probe_one_server(
    server: UpstreamMcpServer,
    timeout: timedelta,
    *,
    preflight_http: HttpPreflightFn,
    list_stdio_tools: Callable[[UpstreamMcpServer, timedelta], list[str]],
) -> int:
    if server.transport == "http":
        if not server.url:
            raise ValueError(f"upstream server '{server.name}' is missing 'url'")
        preflight_http(server.url, (), timeout)
        # The preflight helper does not return tool counts, so probe again to
        # enumerate tools for diagnostic display.
        client = make_upstream_client(server)
        return len(client.list_tools())
    if server.transport == "stdio":
        if not server.command:
            raise ValueError(f"upstream server '{server.name}' is missing 'command'")
        return len(list_stdio_tools(server, timeout))
    raise ValueError(
        f"upstream server '{server.name}' has unsupported transport '{server.transport}'"
    )


__all__ = [
    "UpstreamServerReport",
    "UpstreamValidationError",
    "UpstreamValidationReport",
    "strict_mode_from_env",
    "validate_upstream_mcp_servers",
]

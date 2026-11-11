
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

        def __call__(
            self, endpoint: str, required_tools: tuple[str, ...], timeout: timedelta
        ) -> None: ...


_STRICT_ENV_VAR = "RALPH_MCP_STRICT"
_STRICT_FALSE_VALUES = frozenset({"0", "false", "no", "off"})


@dataclass(frozen=True)
class UpstreamValidationReport:

    servers: tuple[UpstreamServerReport, ...]

    @property
    def all_ok(self) -> bool:
        return all(s.ok for s in self.servers)

    @property
    def failures(self) -> tuple[UpstreamServerReport, ...]:
        return tuple(s for s in self.servers if not s.ok)


def strict_mode_from_env(env: Mapping[str, str] | None = None) -> bool:

    env_map = os.environ if env is None else env
    raw = env_map.get(_STRICT_ENV_VAR)
    if raw is None:
        return True
    return raw.strip().lower() not in _STRICT_FALSE_VALUES


def _list_stdio_tools(server: UpstreamMcpServer, timeout: timedelta) -> list[str]:

    del timeout
    client = make_upstream_client(server)
    return [tool.name for tool in client.list_tools()]


def _redact_error(server: UpstreamMcpServer, exc: BaseException) -> str:

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

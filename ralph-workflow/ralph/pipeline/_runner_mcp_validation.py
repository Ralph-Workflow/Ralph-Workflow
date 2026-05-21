"""MCP server validation helpers for the pipeline runner."""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

import ralph.mcp.transport.claude as claude_transport_module
from ralph.mcp.transport import common as transport_common_module
from ralph.mcp.upstream.agent_probe import AgentProbeReport, probe_agent_transports
from ralph.mcp.upstream.validation import (
    UpstreamValidationError,
    strict_mode_from_env,
    validate_upstream_mcp_servers,
)

if TYPE_CHECKING:
    from collections.abc import Iterable
    from pathlib import Path
    from typing import Protocol

    from ralph.mcp.upstream.config import UpstreamMcpServer
    from ralph.mcp.upstream.validation import UpstreamValidationReport

    class _ValidateMcpFn(Protocol):
        def __call__(
            self, servers: Iterable[UpstreamMcpServer], *, strict: bool
        ) -> UpstreamValidationReport: ...

    class _ProbeTransportsFn(Protocol):
        def __call__(
            self,
            servers: Iterable[UpstreamMcpServer],
            *,
            workspace_path: Path | None,
        ) -> tuple[AgentProbeReport, ...]: ...


def default_validate_mcp(
    servers: Iterable[UpstreamMcpServer], *, strict: bool
) -> UpstreamValidationReport:
    return validate_upstream_mcp_servers(servers, strict=strict)


def default_probe_agent_transports(
    servers: Iterable[UpstreamMcpServer], *, workspace_path: Path | None
) -> tuple[AgentProbeReport, ...]:
    return probe_agent_transports(servers, workspace_path=workspace_path)


def _effective_session_mcp_servers_for_runner_validation(
    workspace_root: Path,
) -> tuple[UpstreamMcpServer, ...]:
    """Return the effective session MCP server set used by agent transports.

    Product terminology:
    - servers from ``mcp.toml`` are Ralph custom MCP servers
    - servers imported from agent-native config (for example Claude ``.mcp.json``)
      are upstream MCP servers

    The runtime child ultimately consumes one combined session MCP set, so runner
    preflight must validate that effective set before agent invocation.
    """
    custom_mcp_servers = transport_common_module.mcp_toml_as_upstreams(workspace_root)
    claude_upstream_servers = claude_transport_module.load_existing_claude_upstream_servers(
        workspace_root
    )
    return transport_common_module.merge_mcp_toml_into_upstreams(
        claude_upstream_servers,
        custom_mcp_servers,
    )


def run_custom_mcp_validation(
    workspace_root: Path,
    validate_fn: _ValidateMcpFn,
    probe_fn: _ProbeTransportsFn,
) -> int:
    """Validate the effective session MCP server set and agent transports.

    Returns the exit code the runner should propagate (0 to continue, 1 to abort).
    """
    upstreams = _effective_session_mcp_servers_for_runner_validation(workspace_root)
    if not upstreams:
        return 0

    strict = strict_mode_from_env()
    try:
        upstream_report = validate_fn(upstreams, strict=strict)
    except UpstreamValidationError as exc:
        logger.error("Custom MCP servers failed startup validation:\n{}", exc)
        return 1

    healthy_names = {r.name for r in upstream_report.servers if r.ok}
    healthy_servers = tuple(s for s in upstreams if s.name in healthy_names)
    if not healthy_servers:
        return 0

    probe_results = probe_fn(healthy_servers, workspace_path=workspace_root)
    failures = [p for p in probe_results if not p.ok]
    if failures and strict:
        for failure in failures:
            logger.error(
                "Agent transport probe failed: server={} transport={} error={}",
                failure.server_name,
                failure.transport,
                failure.error,
            )
        return 1
    for failure in failures:
        logger.warning(
            "Agent transport probe failed (soft mode): server={} transport={} error={}",
            failure.server_name,
            failure.transport,
            failure.error,
        )
    return 0

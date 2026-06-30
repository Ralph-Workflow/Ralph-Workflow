"""Standalone MCP HTTP server runtime for Ralph tools.

Runs the Ralph MCP server as a long-lived HTTP process that AI agents connect
to over the MCP protocol. The server exposes Ralph's tool registry (file
operations, git commands, artifact submission, coordination, etc.) through
the production streamable-HTTP transport (``_FallbackStandaloneServer``).

The architecture is intentionally single-path: there is exactly one server
transport — the production ``_FallbackHttpHandler`` (constructed by
``_FallbackStandaloneServer``) — and every behavior (tool dispatch,
streaming, session handling, concurrency control, error framing) lives on
that one path. See ``docs/agents/architecture.md`` for the rationale.

Key responsibilities:

- ``RalphmcpServer`` - the main server class; call ``start(config)`` to launch
  and ``stop()`` to shut down gracefully. A health-check endpoint listens on
  ``/health``; liveness is polled by ``ralph.process.mcp_supervisor``.
- Environment handshake: the server reads ``MCP_SESSION`` (session JSON) and
  ``MCP_SESSION_FILE`` env vars to populate the agent session, which governs
  which capabilities and upstream MCP servers are enabled.
- Tool capability filtering: tools are registered or skipped based on the
  session's declared ``McpCapability`` set so each agent only sees the tools
  it needs.
- Upstream MCP registry: ``load_upstream_mcp_servers`` discovers additional
  MCP servers from ``UPSTREAM_MCP_CONFIG`` and mounts them alongside Ralph
  tools.

The server is launched by ``ralph.process.manager`` via the
``ralph-mcp`` entry point (``ralph/mcp/server/__main__.py``).
"""

from __future__ import annotations

import argparse
import os
import uuid
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph import __version__
from ralph.agents.system_clock import SystemClock
from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.protocol.capability_mapping import Capability, McpCapability
from ralph.mcp.protocol.env import MAX_SESSION_SECONDS_ENV, SESSION_SOFT_WRAPUP_SECONDS_ENV
from ralph.mcp.protocol.session import AgentSession, McpSession
from ralph.mcp.server._fallback_standalone_server import _FallbackStandaloneServer
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._runtime_constants import (
    DEFAULT_HOST,
    DEFAULT_MOUNT_PATH,
    DEFAULT_PORT,
    DEFAULT_TRANSPORT,
)
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server._session_wrapup import SessionWrapupBudget
from ralph.mcp.server._standalone_http_server import _StandaloneHttpServer
from ralph.mcp.server.runtime_session import FileBackedSession, session_from_env
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.mcp.transport.common import mcp_config_as_upstreams, merge_mcp_toml_into_upstreams
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UPSTREAM_MCP_TOOL_CATALOG_ENV,
    UpstreamMcpServer,
    load_upstream_mcp_servers,
    load_upstream_tool_catalog,
)
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.timeout_defaults import MAX_SESSION_SECONDS, SESSION_SOFT_WRAPUP_SECONDS
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence

    from ralph.config.mcp_models import McpConfig


@dataclass(frozen=True)
class McpServerExtras:
    """Optional DI parameters for building standalone MCP servers.

    The dataclass is the dependency-injection bundle threaded through
    ``build_standalone_http_server`` and ``run_standalone_server``.
    Every field is optional (``None`` means "use the production
    default") so callers can override exactly one seam (e.g. an
    upstream registry for a test) without rebuilding the rest of the
    composition root.

    Attributes:
        session: Pre-built ``McpSession`` (handshake, capabilities,
            upstream mounts). When ``None`` the runtime reads
            ``MCP_SESSION`` / ``MCP_SESSION_FILE`` from the environment
            via ``session_from_env``.
        upstream_registry: Pre-loaded ``UpstreamRegistry`` for
            upstream MCP servers. When ``None`` the runtime loads the
            registry from ``UPSTREAM_MCP_CONFIG`` /
            ``UPSTREAM_MCP_TOOL_CATALOG_ENV`` via
            ``load_runtime_upstream_servers``.
        mcp_config: Pre-parsed ``McpConfig`` (mcp.toml model). When
            ``None`` the runtime calls ``load_mcp_config`` with the
            user-global path.
    """

    session: McpSession | None = None
    upstream_registry: UpstreamRegistry | None = None
    mcp_config: McpConfig | None = None


FallbackStandaloneServer = _FallbackStandaloneServer


def build_standalone_http_server(
    workspace_root: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    extras: McpServerExtras | None = None,
) -> _StandaloneHttpServer:
    """Build a standalone HTTP MCP server backed by the Ralph tool registry."""
    _extras = extras or McpServerExtras()
    effective_session = _extras.session or AgentSession(
        session_id=f"standalone-{uuid.uuid4().hex[:8]}",
        run_id=str(uuid.uuid4()),
        drain="standalone",
        capabilities=_all_capability_values(),
    )
    allowed_roots = cast("tuple[Path, ...]", getattr(effective_session, "allowed_roots", ()))
    workspace = FsWorkspace(
        workspace_root,
        allowed_roots=allowed_roots if allowed_roots else None,
    )
    mcp_cfg = (
        _extras.mcp_config
        if _extras.mcp_config is not None
        else load_mcp_config(config_path=_workspace_mcp_config_path(workspace_root))
    )
    upstream_servers = load_runtime_upstream_servers(mcp_cfg)
    tool_catalog = load_upstream_tool_catalog(os.environ.get(UPSTREAM_MCP_TOOL_CATALOG_ENV))
    if tool_catalog:
        upstream_servers = tuple(
            server for server in upstream_servers if server.name in tool_catalog
        )
    if _extras.upstream_registry is not None:
        upstream_reg = _extras.upstream_registry
    elif upstream_servers and tool_catalog:
        upstream_reg = UpstreamRegistry.build_from_tool_catalog(upstream_servers, tool_catalog)
    elif upstream_servers:
        upstream_reg = UpstreamRegistry.build(upstream_servers)
    else:
        upstream_reg = None
    registry = build_ralph_tool_registry(
        effective_session,
        workspace,
        upstream_registry=upstream_reg,
        mcp_config=mcp_cfg,
    )
    n_builtin = len(list(registry.list_definitions()))
    if upstream_reg and upstream_servers:
        n_proxied = len(list(upstream_reg.tool_definitions()))
        n_servers = len(upstream_servers)
        logger.info(
            "MCP server started with {n} built-in tools + "
            "{m} proxied upstream tools from {k} servers",
            n=n_builtin,
            m=n_proxied,
            k=n_servers,
        )
    else:
        logger.info("MCP server started with {n} built-in tools", n=n_builtin)
    server = McpServer(
        effective_session,
        workspace,
        registry,
        wrapup_provider=_session_wrapup_provider(),
    )
    return _StandaloneHttpServer(host, port, server)


def _env_float(name: str, default: float | None) -> float | None:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    try:
        return float(raw)
    except ValueError:
        return default


def _session_wrapup_provider() -> Callable[[], str | None]:
    """Build the graduated-session wrap-up nag provider from env (or defaults).

    The standalone MCP server starts per agent invocation, so process-start is a
    sound proxy for invocation-start. ``RALPH_SESSION_SOFT_WRAPUP_SECONDS`` and
    ``RALPH_MAX_SESSION_SECONDS`` override the built-in graduated defaults.
    """
    budget = SessionWrapupBudget(
        SystemClock(),
        soft_seconds=_env_float(SESSION_SOFT_WRAPUP_SECONDS_ENV, SESSION_SOFT_WRAPUP_SECONDS),
        hard_seconds=_env_float(MAX_SESSION_SECONDS_ENV, MAX_SESSION_SECONDS),
    )
    return budget.notice


def _all_capability_values() -> set[str]:
    values = {cap.value for cap in Capability}
    values.update(cap.value for cap in McpCapability)
    return values


def _workspace_mcp_config_path(workspace_root: Path) -> Path:
    return workspace_root / ".agent" / "mcp.toml"


def _mcp_toml_upstream_servers(mcp_config: McpConfig) -> tuple[UpstreamMcpServer, ...]:
    return mcp_config_as_upstreams(mcp_config)


def load_runtime_upstream_servers(
    mcp_config: McpConfig,
    env: Mapping[str, str] | None = None,
) -> tuple[UpstreamMcpServer, ...]:
    """Merge upstream MCP servers from the environment variable and mcp.toml."""
    env_map = os.environ if env is None else env
    raw_upstream = env_map.get(UPSTREAM_MCP_CONFIG_ENV)
    env_servers = load_upstream_mcp_servers(raw_upstream)
    return merge_mcp_toml_into_upstreams(env_servers, _mcp_toml_upstream_servers(mcp_config))


def run_standalone_server(
    workspace_root: Path,
    *,
    transport: str = DEFAULT_TRANSPORT,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
) -> None:
    """Run the standalone Ralph MCP server over HTTP."""
    if transport != DEFAULT_TRANSPORT:
        raise ValueError(f"Unsupported transport: {transport}")

    server = build_standalone_http_server(
        workspace_root,
        host=host,
        port=port,
        extras=McpServerExtras(session=session_from_env()),
    )
    print(f"Ralph MCP server listening on http://{host}:{port}{DEFAULT_MOUNT_PATH}")
    server.run(transport=DEFAULT_TRANSPORT)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    """Parse standalone MCP server CLI arguments."""
    parser = argparse.ArgumentParser(description="Run the standalone Ralph MCP HTTP server")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Workspace root exposed to Ralph MCP tools",
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP bind host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP bind port")
    return parser.parse_args(list(argv) if argv is not None else None)


def main(argv: Sequence[str] | None = None) -> None:
    """CLI entrypoint for the standalone Ralph MCP HTTP server.

    The handler is the ``ralph-mcp`` console script entry point
    declared in ``pyproject.toml`` (``ralph-mcp =
    ralph.mcp.server.runtime:main``). It parses ``--workspace``,
    ``--host``, and ``--port`` via ``parse_args`` and delegates to
    ``run_standalone_server`` with the production
    ``DEFAULT_TRANSPORT`` (``streamable-http``). The environment
    handshake (``MCP_SESSION`` / ``MCP_SESSION_FILE``) is performed
    inside ``run_standalone_server``; this entry point does not touch
    the environment directly.

    Args:
        argv: Optional argv override. When ``None``,
            ``argparse`` reads from ``sys.argv[1:]``; tests pass a
            sequence of strings to avoid mutating process state.

    Returns:
        ``None``. The handler blocks until the server is shut down
        (Ctrl-C / SIGTERM). The process exit code is whatever the
        underlying HTTP server / signal handler produces.

    Side effects:
        Starts a long-lived HTTP server bound to ``--host:--port`` and
        a ``/health`` endpoint; the liveness probe is polled by
        ``ralph.process.mcp_supervisor``. Reads the MCP session
        environment variables.
    """
    args = parse_args(argv)
    run_standalone_server(
        cast("Path", args.workspace),
        transport=DEFAULT_TRANSPORT,
        host=cast("str", args.host),
        port=cast("int", args.port),
    )


__all__ = [
    "DEFAULT_HOST",
    "DEFAULT_MOUNT_PATH",
    "DEFAULT_PORT",
    "DEFAULT_TRANSPORT",
    "FallbackStandaloneServer",
    "FileBackedSession",
    "JsonRpcRequest",
    "McpServer",
    "McpServerExtras",
    "ServerState",
    "__version__",
    "build_standalone_http_server",
    "load_runtime_upstream_servers",
    "main",
    "parse_args",
    "run_standalone_server",
    "session_from_env",
]

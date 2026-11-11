
from __future__ import annotations

import argparse
import atexit
import concurrent.futures
import contextlib
import os
import signal
import sys
import threading
import time
import uuid
from dataclasses import dataclass
from pathlib import Path
from threading import Event
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph import __version__
from ralph.config.enums import AgentTransport
from ralph.config.mcp_loader import load_mcp_config
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    MultimodalModelIdentity,
)
from ralph.mcp.protocol._startup_timeouts import (
    _CONFIG_LOAD_BUDGET_S,
    _PROBE_SAFETY_S,
    _READY_TO_SERVE_BUDGET_S,
    _REGISTRY_BUILD_BUDGET_S,
    _check_real_startup_deadline,
    mcp_discovery_budget_from_env,
    mcp_preflight_timeout_from_env,
    mcp_real_startup_deadline_from_env,
    mcp_registry_read_timeout_from_env,
    mcp_whole_startup_budget_from_env,
    record_stage_elapsed,
)
from ralph.mcp.protocol.capability_mapping import Capability, McpCapability
from ralph.mcp.protocol.env import (
    MCP_OWNER_PID_ENV,
    MCP_RESPAWN_ENV,
)
from ralph.mcp.protocol.session import AgentSession, McpSession
from ralph.mcp.server._activity_relay_sender import (
    ActivityRelaySender,
    scrub_activity_relay_environment,
)
from ralph.mcp.server._fallback_standalone_server import _FallbackStandaloneServer
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._pre_serve_overrun import (
    PRE_SERVE_OVERRUN_EXIT_CODE,
    SESSION_OVERRUN_EXIT_CODE,
    fail_closed_pre_serve_overrun,
)
from ralph.mcp.server._pre_serve_overrun import (
    log_and_record_stage as _log_and_record_stage,
)
from ralph.mcp.server._process_secrecy import (
    erase_broker_secret_environment,
    protect_broker_secret,
)
from ralph.mcp.server._publish_catalog_bounded import publish_tool_catalog_bounded
from ralph.mcp.server._reserved_socket import _adopt_inherited_socket
from ralph.mcp.server._runtime_constants import (
    DEFAULT_HOST,
    DEFAULT_MOUNT_PATH,
    DEFAULT_PORT,
    DEFAULT_TRANSPORT,
)
from ralph.mcp.server._runtime_hooks import (
    _OWNER_LOSS_HOOKS,
    _SHUTDOWN_HOOKS,
    register_owner_loss_hook,
    register_shutdown_hook,
)
from ralph.mcp.server._runtime_polling import (
    _poll_registry_build_gate as _base_poll_registry_build_gate,
)
from ralph.mcp.server._server_state import ServerState
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
from ralph.mcp.upstream.validation import strict_mode_from_env
from ralph.process._spawn_env import sanitize_process_environment
from ralph.timeout_defaults import EXEC_OWNER_LOSS_HOOK_BUDGET_S
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    import socket
    from collections.abc import Callable, Mapping, MutableMapping, Sequence

    from ralph.config.mcp_models import McpConfig


@dataclass(frozen=True)
class McpServerExtras:

    session: McpSession | None = None
    upstream_registry: UpstreamRegistry | None = None
    mcp_config: McpConfig | None = None
    workspace: FsWorkspace | None = None


FallbackStandaloneServer = _FallbackStandaloneServer

_clock = time.monotonic

_REGISTRY_BUILD_GATE_HARD_DEADLINE_S: float = 0.05

if not (mcp_whole_startup_budget_from_env() < mcp_preflight_timeout_from_env().total_seconds()):
    raise RuntimeError(
        "import-time invariant violated: mcp_whole_startup_budget_from_env() "
        "must be strictly less than mcp_preflight_timeout_from_env()"
    )


def standalone_session_identity(agent_transport: str | None) -> MultimodalModelIdentity:
    declared = (agent_transport or "").strip().lower()
    if not declared:
        return UNKNOWN_IDENTITY
    return MultimodalModelIdentity(
        provider=UNKNOWN_IDENTITY.provider,
        model_id=None,
        transport=declared,
    )


def _build_mcp_registry_and_server(
    *,
    effective_session: McpSession,
    workspace: FsWorkspace,
    upstream_reg: UpstreamRegistry | None,
    upstream_servers: Sequence[UpstreamMcpServer],
    mcp_cfg: McpConfig,
    activity_relay_sender: ActivityRelaySender | None,
    t0: float,
    env_map: Mapping[str, str],
) -> McpServer:
    real_deadline_s = mcp_real_startup_deadline_from_env(env_map)
    remaining_budget_s = max(0.0, real_deadline_s - (_clock() - t0))
    _read_timeout_ceiling_s = mcp_registry_read_timeout_from_env(env_map)
    effective_read_timeout_s = remaining_budget_s

    t_reg_build_start = _clock()
    reg = build_ralph_tool_registry(
        effective_session,
        workspace,
        upstream_registry=upstream_reg,
        mcp_config=mcp_cfg,
        t0=t0,
        budget_s=remaining_budget_s,
        timeout_s=effective_read_timeout_s,
        clock_fn=_clock,
    )
    per_stage_build_s = _clock() - t_reg_build_start
    headroom_s = mcp_real_startup_deadline_from_env(env_map) - (_clock() - t0)
    record_stage_elapsed(
        "registry_built",
        per_stage_build_s,
        budget_s=_REGISTRY_BUILD_BUDGET_S,
        root=workspace.root,
        headroom_s=headroom_s,
    )
    _check_real_startup_deadline("registry_built", t0, env_map)
    n_builtin = len(list(reg.list_definitions()))
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

    whole_startup_budget_s = mcp_whole_startup_budget_from_env(env_map)
    _poll_registry_build_gate(env_map, t0, whole_startup_budget_s)

    return McpServer(
        effective_session,
        workspace,
        reg,
        mcp_activity_sink=(
            activity_relay_sender.emit if activity_relay_sender is not None else None
        ),
    )


def _poll_registry_build_gate(
    env_map: Mapping[str, str],
    t0: float,
    budget_s: float,
) -> None:
    _base_poll_registry_build_gate(env_map, t0, budget_s, clock_fn=_clock)


def _poll_bounded_pre_serve(
    *,
    workspace_root: Path,
    effective_session: McpSession,
    workspace: FsWorkspace | None,
    extras: McpServerExtras,
    env_map: Mapping[str, str],
    t0: float,
) -> tuple[FsWorkspace, McpConfig]:
    real_deadline_s = mcp_preflight_timeout_from_env(env_map).total_seconds() - _PROBE_SAFETY_S
    remaining_s = max(0.0, real_deadline_s - (_clock() - t0))
    effective_timeout_s = remaining_s

    def _do_pre_serve() -> tuple[FsWorkspace, McpConfig]:
        allowed_roots = cast(
            "tuple[Path, ...]", getattr(effective_session, "allowed_roots", ())
        )
        ws = (
            workspace
            if workspace is not None
            else (
                extras.workspace
                if extras.workspace is not None
                else FsWorkspace(
                    workspace_root, allowed_roots=allowed_roots if allowed_roots else None
                )
            )
        )
        cfg = (
            extras.mcp_config
            if extras.mcp_config is not None
            else load_mcp_config(config_path=_workspace_mcp_config_path(workspace_root))
        )
        return (ws, cfg)

    ex: concurrent.futures.ThreadPoolExecutor | None = None
    try:
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = ex.submit(_do_pre_serve)
        return future.result(timeout=effective_timeout_s)
    except concurrent.futures.TimeoutError:
        elapsed = _clock() - t0
        fail_closed_pre_serve_overrun(
            workspace_root,
            stage="config_loading",
            elapsed_s=elapsed,
            budget_s=effective_timeout_s,
            exit_code=PRE_SERVE_OVERRUN_EXIT_CODE,
        )
    except Exception:
        raise
    finally:
        if ex is not None:
            ex.shutdown(wait=False)


def build_standalone_http_server(
    workspace_root: Path,
    *,
    host: str = DEFAULT_HOST,
    port: int = DEFAULT_PORT,
    workspace: FsWorkspace | None = None,
    extras: McpServerExtras | None = None,
    agent_transport: str | None = None,
    activity_relay_sender: ActivityRelaySender | None = None,
    env: Mapping[str, str] | None = None,
    parent_socket: socket.socket | None = None,
    t0: float | None = None,
) -> _StandaloneHttpServer:
    _extras = extras or McpServerExtras()
    env_map = os.environ if env is None else env
    if t0 is None:
        t0 = _clock()
    effective_session = _extras.session or AgentSession(
        session_id=f"standalone-{uuid.uuid4().hex[:8]}",
        run_id=str(uuid.uuid4()),
        drain="standalone",
        capabilities=_all_capability_values(),
        model_identity=standalone_session_identity(agent_transport),
    )

    t0_eff = t0 if t0 is not None else _clock()

    def _stage(name: str) -> None:
        _log_and_record_stage(
            workspace_root,
            name,
            t0=t0_eff,
            env_map=env_map,
            clock_fn=_clock,
        )

    _stage("config_loading")
    _check_real_startup_deadline("config_loading", t0_eff, env_map)
    t_stage_config = _clock()
    effective_workspace, mcp_cfg = _poll_bounded_pre_serve(
        workspace_root=workspace_root,
        effective_session=effective_session,
        workspace=workspace,
        extras=_extras,
        env_map=env_map,
        t0=t0,
    )
    _stage("config_loaded")
    record_stage_elapsed(
        "config_loaded",
        _clock() - t_stage_config,
        budget_s=_CONFIG_LOAD_BUDGET_S,
        root=workspace_root,
        headroom_s=mcp_real_startup_deadline_from_env(env_map) - (_clock() - t0),
    )
    _check_real_startup_deadline("config_loaded", t0, env_map)

    t_stage_discovery = _clock()
    upstream_servers = load_runtime_upstream_servers(mcp_cfg)
    catalog_raw = env_map.get(UPSTREAM_MCP_TOOL_CATALOG_ENV)
    catalog_present = catalog_raw is not None
    if catalog_present:
        tool_catalog = load_upstream_tool_catalog(catalog_raw)
        upstream_servers = tuple(
            server for server in upstream_servers if server.name in tool_catalog
        )
    else:
        tool_catalog = {}
    is_respawn = env_map.get(MCP_RESPAWN_ENV) == "1"
    if is_respawn and upstream_servers and not catalog_present:
        _stage("respawn_precheck")
        raise McpServerError(
            "MCP respawn missing tool catalog (RALPH_MCP_RESPAWN=1 set but no "
            "UPSTREAM_MCP_TOOL_CATALOG_ENV); refusing to probe upstreams during "
            "respawn (servers={[s.name for s in upstream_servers]})",
            restart_count=0,
            last_stage="respawn_precheck",
        )

    if _extras.upstream_registry is not None:
        upstream_reg = _extras.upstream_registry
    elif upstream_servers and catalog_present:
        upstream_reg = UpstreamRegistry.build_from_tool_catalog(upstream_servers, tool_catalog)
    elif upstream_servers:
        try:
            upstream_reg = UpstreamRegistry.build(
                upstream_servers,
                on_unreachable="raise" if strict_mode_from_env(env_map) else "warn_and_skip",
            )
            catalog_payload = {
                s.name: [
                    pt.tool for pt in upstream_reg.tool_definitions() if pt.server_name == s.name
                ]
                for s in upstream_servers
            }
            publish_tool_catalog_bounded(workspace_root, catalog_payload, env_map, t0=t0)
        except McpServerError:
            (workspace_root / ".agent" / "mcp" / "parent_catalog.json").unlink(missing_ok=True)
            raise
        except Exception as exc:
            (workspace_root / ".agent" / "mcp" / "parent_catalog.json").unlink(missing_ok=True)
            raise McpServerError(
                f"first-start upstream discovery failed: {exc}",
                restart_count=0,
                last_stage="upstream_catalog_or_discovery_done",
            ) from exc
    else:
        upstream_reg = None
    if upstream_reg is not None and upstream_servers:
        n_proxied = len(list(upstream_reg.tool_definitions()))
        n_servers = len(upstream_servers)
        logger.info(
            "ralph-mcp startup: stage={stage} n_servers={k} n_proxied_tools={m}",
            stage="upstream_catalog_or_discovery_done",
            k=n_servers,
            m=n_proxied,
        )
    _stage("upstream_catalog_or_discovery_done")
    record_stage_elapsed(
        "upstream_catalog_or_discovery_done",
        _clock() - t_stage_discovery,
        budget_s=mcp_discovery_budget_from_env(env_map).total_seconds(),
        root=workspace_root,
        headroom_s=mcp_real_startup_deadline_from_env(env_map) - (_clock() - t0),
    )
    _check_real_startup_deadline("upstream_catalog_or_discovery_done", t0, env_map)

    t_stage_reg = _clock()
    _stage("registry_built")
    _check_real_startup_deadline("registry_built", t0, env_map)

    server = _build_mcp_registry_and_server(
        effective_session=effective_session,
        workspace=effective_workspace,
        upstream_reg=upstream_reg,
        upstream_servers=upstream_servers,
        mcp_cfg=mcp_cfg,
        activity_relay_sender=activity_relay_sender,
        t0=t0,
        env_map=env_map,
    )
    record_stage_elapsed(
        "registry_built",
        _clock() - t_stage_reg,
        budget_s=_REGISTRY_BUILD_BUDGET_S,
        root=workspace_root,
        headroom_s=mcp_real_startup_deadline_from_env(env_map) - (_clock() - t0),
    )
    _check_real_startup_deadline("registry_built", t0, env_map)

    return _StandaloneHttpServer(host, port, server, parent_socket=parent_socket)


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
    agent_transport: str | None = None,
    ready_event: Event | None = None,
    stop_event: Event | None = None,
    env: Mapping[str, str] | None = None,
) -> None:
    if transport != DEFAULT_TRANSPORT:
        raise ValueError(f"Unsupported transport: {transport}")

    env_map = os.environ if env is None else env
    t0 = _clock()

    def _stage(name: str) -> None:
        _log_and_record_stage(
            workspace_root,
            name,
            t0=t0,
            env_map=env_map,
            clock_fn=_clock,
        )

    _stage("env_loaded")
    _check_real_startup_deadline("env_loaded", t0, env_map)

    with contextlib.suppress(ValueError, AttributeError, OSError):
        reconfigure_fn: object = getattr(sys.stdout, "reconfigure", None)
        if callable(reconfigure_fn):
            reconfigure_fn(line_buffering=True)

    protect_broker_secret()
    sanitize_process_environment()
    relay_sender = ActivityRelaySender.from_environment(dict(os.environ))
    scrub_activity_relay_environment(os.environ)

    real_deadline_s = mcp_real_startup_deadline_from_env(env_map)
    remaining_s = max(0.0, real_deadline_s - (_clock() - t0))
    session_timeout_s = remaining_s

    ex: concurrent.futures.ThreadPoolExecutor | None = None
    try:
        ex = concurrent.futures.ThreadPoolExecutor(max_workers=1)
        future = ex.submit(
            session_from_env,
            declared_agent_transport=agent_transport,
            workspace_root=workspace_root,
        )
        session = future.result(timeout=session_timeout_s)
    except concurrent.futures.TimeoutError:
        elapsed = _clock() - t0
        fail_closed_pre_serve_overrun(
            workspace_root,
            stage="session_ready",
            elapsed_s=elapsed,
            budget_s=session_timeout_s,
            exit_code=SESSION_OVERRUN_EXIT_CODE,
        )
    finally:
        if ex is not None:
            ex.shutdown(wait=False)

    erase_broker_secret_environment()
    _stage("session_ready")
    _check_real_startup_deadline("session_ready", t0, env_map)

    parent_socket = _adopt_inherited_socket(env_map, t0, _stage, clock_fn=_clock)

    is_respawn = env_map.get(MCP_RESPAWN_ENV) == "1"
    server = build_standalone_http_server(
        workspace_root,
        host=host,
        port=port,
        extras=McpServerExtras(session=session),
        agent_transport=agent_transport,
        activity_relay_sender=relay_sender,
        env=env_map,
        parent_socket=parent_socket,
        t0=t0,
    )
    if is_respawn:
        logger.info(
            "ralph-mcp respawn on inherited FD (no network discovery, "
            "registry built from cached tool catalog)"
        )

    t_stage_ready = _clock()

    def _record_ready_to_serve() -> None:
        _check_real_startup_deadline("ready_to_serve", t0, env_map)
        per_stage_ready_s = _clock() - t_stage_ready
        headroom_s = mcp_real_startup_deadline_from_env(env_map) - (_clock() - t0)
        record_stage_elapsed(
            "ready_to_serve",
            per_stage_ready_s,
            budget_s=_READY_TO_SERVE_BUDGET_S,
            root=workspace_root,
            headroom_s=headroom_s,
        )
        _stage("ready_to_serve")

    install_shutdown_signal_handlers()
    try:
        if stop_event is not None:
            server.run(
                transport=DEFAULT_TRANSPORT,
                ready_event=ready_event,
                stop_event=stop_event,
                on_ready=_record_ready_to_serve,
            )
        else:
            server.run(
                transport=DEFAULT_TRANSPORT,
                ready_event=ready_event,
                on_ready=_record_ready_to_serve,
            )
    finally:
        _run_shutdown_hooks()


def _agent_transport_choices() -> list[str]:
    values: list[str] = [transport.value for transport in AgentTransport]
    return sorted(values)


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Run the standalone Ralph MCP HTTP server")
    parser.add_argument(
        "--workspace",
        type=Path,
        default=Path.cwd(),
        help="Workspace root exposed to Ralph MCP tools",
    )
    parser.add_argument(
        "--agent-transport",
        default=None,
        choices=_agent_transport_choices(),
        help=(
            "Agent CLI this server will serve. Some media-delivery decisions "
            "key on the CLI rather than the provider; declaring it lets the "
            "server withhold content that CLI cannot carry."
        ),
    )
    parser.add_argument("--host", default=DEFAULT_HOST, help="HTTP bind host")
    parser.add_argument("--port", type=int, default=DEFAULT_PORT, help="HTTP bind port")
    return parser.parse_args(list(argv) if argv is not None else None)


def _run_shutdown_hooks() -> None:
    for hook in _SHUTDOWN_HOOKS:
        with contextlib.suppress(Exception):
            hook()


def _run_owner_loss_hooks(timeout_s: float = EXEC_OWNER_LOSS_HOOK_BUDGET_S) -> None:

    def _invoke_all() -> None:
        for hook in _OWNER_LOSS_HOOKS:
            with contextlib.suppress(Exception):
                hook()

    t = threading.Thread(target=_invoke_all, name="mcp-owner-loss-hooks", daemon=True)
    t.start()
    t.join(timeout=timeout_s)


def install_shutdown_signal_handlers() -> None:

    def _sigterm_handler(signum: int, frame: object) -> None:
        _run_shutdown_hooks()
        sys.exit(128 + signal.SIGTERM)

    with contextlib.suppress(ValueError, OSError):
        signal.signal(signal.SIGTERM, _sigterm_handler)


atexit.register(_run_shutdown_hooks)


def exit_when_owner_exits(
    env: MutableMapping[str, str],
    *,
    get_parent_pid: Callable[[], int] = os.getppid,
    sleep: Callable[[float], None] = time.sleep,
    exit_process: Callable[[int], None] = os._exit,
) -> threading.Thread | None:
    raw_owner = env.pop(MCP_OWNER_PID_ENV, "")
    if not raw_owner.isdigit():
        return None
    owner = int(raw_owner)
    if owner != get_parent_pid():
        _run_owner_loss_hooks()
        exit_process(0)
        return None

    def watch() -> None:
        while get_parent_pid() == owner:
            sleep(1.0)
        _run_owner_loss_hooks()
        exit_process(0)

    watcher = threading.Thread(target=watch, name="mcp-owner-watch", daemon=True)
    watcher.start()
    return watcher


def main(argv: Sequence[str] | None = None) -> None:
    sanitize_process_environment()
    install_shutdown_signal_handlers()
    exit_when_owner_exits(os.environ)
    args = parse_args(argv)
    run_standalone_server(
        cast(
            "Path", args.workspace
        ),
        transport=DEFAULT_TRANSPORT,
        host=cast(
            "str", args.host
        ),
        port=cast(
            "int", args.port
        ),
        agent_transport=cast(
            "str | None", args.agent_transport
        ),
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
    "exit_when_owner_exits",
    "install_shutdown_signal_handlers",
    "load_runtime_upstream_servers",
    "main",
    "parse_args",
    "register_owner_loss_hook",
    "register_shutdown_hook",
    "run_standalone_server",
    "session_from_env",
    "standalone_session_identity",
]

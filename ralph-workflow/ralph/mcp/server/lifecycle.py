"""MCP server lifecycle helpers using a standalone localhost HTTP process."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, cast

from loguru import logger

from ralph.mcp.multimodal.capabilities import (
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
    resolve_capability_profile,
)
from ralph.mcp.protocol._session_bridge_like import SessionBridgeLike
from ralph.mcp.protocol.env import MCP_SESSION_FILE_ENV as SESSION_FILE_ENV
from ralph.mcp.protocol.startup import (
    mcp_preflight_timeout_from_env,
    mcp_probe_timeout_from_env,
    preflight_http_mcp_server_tools,
    probe_mcp_http_endpoint,
)
from ralph.mcp.server._lifecycle_deps import LifecycleDeps
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._mcp_server_error import McpServerError
from ralph.mcp.server._mcp_server_extras import McpServerExtras
from ralph.mcp.server._process_like import ProcessLike
from ralph.mcp.server._spawn_process import SpawnProcess
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.tools.bridge import build_ralph_tool_registry
from ralph.process.manager import ManagedProcess, SpawnOptions, get_process_manager
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.protocol.startup import SessionLike, WorkspaceLike
    from ralph.mcp.upstream.registry import UpstreamRegistry

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]


class RestartAwareMcpBridge:
    """SessionBridgeLike wrapper that auto-restarts the MCP server on crash.

    Bounded restart budget prevents unbounded retry loops. All process
    spawning continues to flow through ProcessManager via the injected
    LifecycleDeps so ProcessManager remains the single process authority.

    The endpoint URI is stable for the full bridge lifetime: the same host/port
    is reused on every restart so agents never see a changed MCP_ENDPOINT_ENV.
    Thread-safe: a lock guards all inner-process mutations so the background
    McpSupervisor can safely call check_health_and_restart_if_needed() while
    the main thread is executing agent output streaming.
    """

    def __init__(
        self,
        inner: StandaloneMcpProcess,
        *,
        restart_fn: Callable[[], StandaloneMcpProcess],
        restart_policy: McpRestartPolicy,
        probe_fn: Callable[[str, timedelta], None] | None = None,
        probe_timeout_fn: Callable[[], timedelta] | None = None,
    ) -> None:
        self._inner = inner
        self._restart_fn = restart_fn
        self._restart_policy = restart_policy
        self._probe_fn = probe_fn
        self._probe_timeout_fn = probe_timeout_fn
        self._restart_count = 0
        self._lock = threading.Lock()

    @property
    def restart_count(self) -> int:
        return self._restart_count

    def start(self) -> None:
        return

    def agent_endpoint_uri(self) -> str:
        return self._inner.endpoint

    def endpoint_uri(self) -> str:
        return self._inner.endpoint

    def shutdown(self) -> None:
        self._inner.shutdown()

    def check_health_and_restart_if_needed(self) -> bool:
        """Check if MCP server is alive and responsive; restart if not.

        Treats the bridge as unhealthy when either (a) the subprocess has exited
        or (b) the subprocess is alive but the responsiveness probe times out or
        fails. On an unhealthy result the stale process is terminated, a new one
        is spawned via restart_fn (which reruns full preflight), and the bounded
        restart counter is incremented.

        Returns True when a restart was performed.
        Raises McpServerError when the restart budget is exhausted.
        Thread-safe: may be called from a background McpSupervisor thread.
        """
        with self._lock:
            process_exited = self._inner.process.poll() is not None
            probe_failed = False
            if not process_exited and self._probe_fn is not None:
                try:
                    probe_timeout = (
                        self._probe_timeout_fn()
                        if self._probe_timeout_fn is not None
                        else timedelta(seconds=5)
                    )
                    self._probe_fn(self._inner.endpoint, probe_timeout)
                except Exception:
                    probe_failed = True

            if not process_exited and not probe_failed:
                return False

            if self._restart_count >= self._restart_policy.max_restarts:
                reason = "probe failed" if probe_failed and not process_exited else "exited"
                raise McpServerError(
                    f"MCP server {reason} and restart budget"
                    f" ({self._restart_policy.max_restarts}) exhausted"
                    f" after {self._restart_count} restart(s)",
                    restart_count=self._restart_count,
                )

            if probe_failed and not process_exited:
                logger.warning(
                    "MCP server unresponsive (probe timed out or failed); restarting ({}/{})",
                    self._restart_count + 1,
                    self._restart_policy.max_restarts,
                )
            else:
                logger.warning(
                    "MCP server process exited unexpectedly; restarting ({}/{})",
                    self._restart_count + 1,
                    self._restart_policy.max_restarts,
                )
            self._inner.shutdown()
            self._inner = self._restart_fn()
            self._restart_count += 1
            logger.info(
                "MCP server restarted on stable endpoint {}; restart_count={}",
                self._inner.endpoint,
                self._restart_count,
            )
            return True


def check_mcp_bridge_health(bridge: SessionBridgeLike) -> None:
    """Perform a health check on the MCP bridge, restarting if it crashed.

    Only has an effect when ``bridge`` is a :class:`RestartAwareMcpBridge`.
    Raises :class:`McpServerError` when the restart budget is exhausted.
    """
    if isinstance(bridge, RestartAwareMcpBridge):
        bridge.check_health_and_restart_if_needed()


def start_mcp_server(
    session: SessionLike,
    workspace: WorkspaceLike,
    *,
    upstream_registry: UpstreamRegistry | None = None,
    deps: LifecycleDeps | None = None,
    extras: McpServerExtras | None = None,
) -> RestartAwareMcpBridge:
    """Start a standalone Ralph MCP HTTP subprocess and verify tool reachability.

    Returns a :class:`RestartAwareMcpBridge` that can auto-restart the server
    on crash up to the ``extras.restart_policy`` budget (default: 1000 restarts).
    """
    effective_extras = extras or McpServerExtras()
    lifecycle_deps = deps or _default_lifecycle_deps()
    root = _workspace_root(workspace)
    visible_tools = _visible_mcp_tool_names_owned(
        session, workspace, upstream_registry=upstream_registry
    )

    # Reserve the port once so the endpoint stays stable across all restarts.
    # The same port is reused by every _restart_fn() call so agents never see a
    # changed MCP_ENDPOINT_ENV value after a mid-run crash and restart.
    port = lifecycle_deps.reserve_port()
    inner = _spawn_mcp_process(
        root,
        session,
        lifecycle_deps,
        effective_extras.phase,
        effective_extras.extra_env,
        visible_tools,
        port=port,
    )

    def _restart_fn() -> StandaloneMcpProcess:
        return _spawn_mcp_process(
            root,
            session,
            lifecycle_deps,
            effective_extras.phase,
            effective_extras.extra_env,
            visible_tools,
            port=port,
        )

    return RestartAwareMcpBridge(
        inner,
        restart_fn=_restart_fn,
        restart_policy=effective_extras.restart_policy or McpRestartPolicy(),
        probe_fn=lifecycle_deps.probe,
        probe_timeout_fn=lifecycle_deps.probe_timeout,
    )


def _spawn_mcp_process(
    root: Path,
    session: SessionLike,
    deps: LifecycleDeps,
    phase: str | None,
    _extra_env: dict[str, str] | None,
    _visible_tools: list[str],
    *,
    port: int,
) -> StandaloneMcpProcess:
    """Spawn a fresh MCP server process and run preflight validation."""
    endpoint = f"http://127.0.0.1:{port}/mcp"
    session_file = deps.create_session_file(root, session)
    env = deps.subprocess_env(session_file)
    if _extra_env:
        # Merge extra_env so the subprocess inherits worker-specific env vars
        # (e.g. WORKER_ARTIFACT_DIR for parallel workers).
        env.update(_extra_env)
    process = deps.spawn_process(
        [
            sys.executable,
            "-m",
            "ralph.mcp.server",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workspace",
            str(root),
        ],
        root,
        env,
        phase=phase,
    )
    bridge = StandaloneMcpProcess(endpoint=endpoint, process=process, session_file=session_file)

    try:
        deps.preflight(endpoint, _visible_tools, deps.preflight_timeout())
    except Exception:
        bridge.shutdown()
        raise

    return bridge


def shutdown_mcp_server(bridge: SessionBridgeLike) -> None:
    """Shutdown MCP server process."""
    bridge.shutdown()


def _visible_mcp_tool_names_owned(
    session: SessionLike,
    workspace: WorkspaceLike,
    *,
    upstream_registry: UpstreamRegistry | None = None,
) -> list[str]:
    registry = build_ralph_tool_registry(
        session, workspace, upstream_registry=upstream_registry, mcp_config=None
    )
    return [definition.name for definition in registry.list_definitions()]


def _workspace_root(workspace: WorkspaceLike) -> Path:
    if isinstance(workspace, FsWorkspace):
        return workspace.root
    root_value = cast("Path | str | None", getattr(workspace, "root", None))
    if isinstance(root_value, Path):
        return root_value.resolve()
    if isinstance(root_value, str):
        return Path(root_value).expanduser().resolve()
    msg = "Workspace root must be explicit when starting the MCP server"
    raise ValueError(msg)


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return cast("int", sock.getsockname()[1])


def _subprocess_env(session_file: Path) -> dict[str, str]:
    env = dict(os.environ)
    env[SESSION_FILE_ENV] = str(session_file)
    pythonpath = env.get("PYTHONPATH")
    package_root = str(_PACKAGE_ROOT)
    env["PYTHONPATH"] = (
        package_root if not pythonpath else os.pathsep.join([package_root, pythonpath])
    )
    return env


def _spawn_process(
    command: list[str], cwd: Path, env: dict[str, str], *, phase: str | None = None
) -> ManagedProcess:
    label = f"phase:{phase}:mcp-server" if phase else "mcp-server"
    return get_process_manager().spawn(
        command,
        SpawnOptions(
            cwd=str(cwd),
            env=env,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            start_new_session=True,
            label=label,
        ),
    )


def _default_lifecycle_deps() -> LifecycleDeps:
    return LifecycleDeps(
        reserve_port=_reserve_port,
        create_session_file=_create_session_file,
        subprocess_env=_subprocess_env,
        spawn_process=_spawn_process,
        preflight=preflight_http_mcp_server_tools,
        preflight_timeout=mcp_preflight_timeout_from_env,
        probe=probe_mcp_http_endpoint,
        probe_timeout=mcp_probe_timeout_from_env,
    )


def _create_session_file(root: Path, session: SessionLike) -> Path:
    session_dir = root / ".agent" / "tmp"
    session_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="ralph-mcp-session-", suffix=".json", dir=session_dir)
    os.close(fd)
    path = Path(temp_path)
    path.write_text(session_payload_json(session), encoding="utf-8")
    return path


def session_payload_json(session: SessionLike) -> str:
    """Serialize the session metadata to a compact JSON string for MCP handshake."""
    session_payload: dict[str, object] = {
        "session_id": session.session_id,
        "run_id": session.run_id,
        "drain": session.drain,
        "capabilities": sorted(session.capabilities),
    }
    raw_identity: object = getattr(session, "model_identity", None)
    if isinstance(raw_identity, MultimodalModelIdentity) and raw_identity.is_known():
        session_payload["model_identity"] = {
            "provider": raw_identity.provider,
            "model_id": raw_identity.model_id,
            "transport": raw_identity.transport,
        }
    raw_profile: object = getattr(session, "capability_profile", None)
    if isinstance(raw_profile, ResolvedCapabilityProfile):
        session_payload["capability_profile"] = raw_profile.to_payload()
    elif isinstance(raw_identity, MultimodalModelIdentity) and raw_identity.is_known():
        session_payload["capability_profile"] = resolve_capability_profile(
            raw_identity
        ).to_payload()
    return json.dumps(session_payload)


__all__ = [
    "LifecycleDeps",
    "McpRestartPolicy",
    "McpServerError",
    "McpServerExtras",
    "ProcessLike",
    "RestartAwareMcpBridge",
    "SessionBridgeLike",
    "SpawnProcess",
    "StandaloneMcpProcess",
    "check_mcp_bridge_health",
    "session_payload_json",
    "shutdown_mcp_server",
    "start_mcp_server",
]

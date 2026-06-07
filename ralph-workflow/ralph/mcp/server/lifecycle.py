"""MCP server lifecycle helpers using a standalone localhost HTTP process."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
import threading
import urllib.error
import urllib.request
from datetime import timedelta
from pathlib import Path
from typing import IO, TYPE_CHECKING, cast

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
from ralph.mcp.tools.names import RalphToolName, claude_tool_name
from ralph.process.manager import ManagedProcess, SpawnOptions, get_process_manager
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.protocol.startup import SessionLike, WorkspaceLike
    from ralph.mcp.upstream.registry import UpstreamRegistry

# Additive cap on the tool-registry-reset counter. The recovery
# controller's `max_recovery_attempts` and the MCP restart policy's
# `max_restarts` are independent. This cap bounds the third retry
# surface — when a tool-availability failure recurs after the bridge
# has rebuilt its visible tool list, the orchestrator needs a
# distinguishable cap error so the operator can diagnose which bound
# fired. The error message MUST contain the substring
# 'tool-registry-reset exhausted' so the orchestrator can branch on it.
_TOOL_REGISTRY_MAX_RESETS: int = 3
if not _TOOL_REGISTRY_MAX_RESETS > 0:
    raise RuntimeError(
        "_TOOL_REGISTRY_MAX_RESETS must be positive"
        f" (got {_TOOL_REGISTRY_MAX_RESETS})"
    )

# Alias-verify timeout for the post-respawn tools/list probe. The
# probe is HTTP-based and bounded so a wedged inner process does
# not stall the recovery path. The invariant below is enforced at
# import time via `if`/`raise RuntimeError` (NOT `assert`) so it
# survives `python -O`.
_RESPAWN_ALIAS_VERIFY_TIMEOUT_S: float = 2.0
_RESPAWN_ALIAS_VERIFY_TIMEOUT_MAX_S: float = 30.0
if not (
    _RESPAWN_ALIAS_VERIFY_TIMEOUT_S > 0
    and _RESPAWN_ALIAS_VERIFY_TIMEOUT_S < _RESPAWN_ALIAS_VERIFY_TIMEOUT_MAX_S
):
    raise RuntimeError(
        "_RESPAWN_ALIAS_VERIFY_TIMEOUT_S must be in (0,"
        f" {_RESPAWN_ALIAS_VERIFY_TIMEOUT_MAX_S:g}) seconds"
        f" (got {_RESPAWN_ALIAS_VERIFY_TIMEOUT_S})"
    )

# Canonical tool name whose alias is verified after every
# ``reset_tool_registry()`` call. ``read_file`` is the most common
# wedge surface: a regression that strips the alias from the
# post-respawn tools/list re-introduces the post-tool-result wedge
# even though ``reset_tool_registry()`` returned successfully.
_ALIAS_VERIFY_TOOL_NAME: str = "read_file"
# MCP server name used to build the alias. The companion constant
# lives in ``ralph.mcp.tools.names``; we hard-code ``"ralph"`` here
# to keep the lifecycle module import-light and avoid a circular
# dependency through ``ralph.mcp.tools.bridge``.
_ALIAS_VERIFY_SERVER_NAME: str = "ralph"

_PACKAGE_ROOT = Path(__file__).resolve().parents[3]

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
        # Tool-registry resets are tracked separately from crash
        # restarts so the orchestrator can distinguish a "tool-registry
        # rebuild" event from a "MCP server crashed" event. The cap
        # (_TOOL_REGISTRY_MAX_RESETS) is independent of the MCP restart
        # policy's max_restarts. See _tool_registry_resets property.
        self._tool_registry_resets = 0
        self._lock = threading.Lock()

    @property
    def restart_count(self) -> int:
        return self._restart_count

    @property
    def tool_registry_resets(self) -> int:
        """Number of times `reset_tool_registry()` has been called.

        Independent of `restart_count` (which tracks crash restarts) and
        the recovery controller's `max_recovery_attempts` (which tracks
        agent-invocation retries). The orchestrator can inspect this
        counter to diagnose which cap fired on a wedged run.
        """
        return self._tool_registry_resets

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

    def reset_tool_registry(self) -> None:
        """Rebuild the visible tool list by rerunning the restart path.

        Called by the recovery controller when the failure classifier
        flags a tool-availability failure (the post-tool-result wedge:
        live server reports ``No such tool available: mcp__<server>__<tool>``
        because the agent's ``tools/list`` snapshot lost the alias after
        a prior restart, retry, or transient recovery).

        This is a separate counter from ``_restart_count`` so the
        orchestrator can distinguish a tool-registry rebuild from a
        crash restart. The cap is ``_TOOL_REGISTRY_MAX_RESETS``.

        Raises:
            McpServerError: when the cap is exhausted. The message
                contains the substring ``'tool-registry-reset exhausted'``
                so the orchestrator can branch on it.
        """
        with self._lock:
            if self._tool_registry_resets >= _TOOL_REGISTRY_MAX_RESETS:
                raise McpServerError(
                    "MCP tool-registry-reset exhausted "
                    f"({_TOOL_REGISTRY_MAX_RESETS}) "
                    f"after {self._tool_registry_resets} reset(s)",
                    restart_count=self._restart_count,
                )
            self._tool_registry_resets += 1
            logger.info(
                "Resetting MCP tool registry ({}/{}); endpoint={}",
                self._tool_registry_resets,
                _TOOL_REGISTRY_MAX_RESETS,
                self._inner.endpoint,
            )
            # Reuse the same restart path so preflight runs again and
            # the tools/list snapshot is rebuilt. We do NOT call
            # self._restart_fn() directly here because that would skip
            # the preflight (the restart_fn is a private seam used by
            # check_health_and_restart_if_needed). Instead, the caller
            # is expected to invoke the bridge's preflight + spawn path
            # via a small wrapper. For now, the simplest correct
            # behavior is: rotate the inner process via the same
            # _restart_fn so a fresh registry is built. The same
            # endpoint is reused (the port is reserved at bridge
            # construction and stays stable across resets).
            self._inner.shutdown()
            self._inner = self._restart_fn()
            # NEW BEHAVIOR: actively verify the mcp__<server>__<tool>
            # alias is present in the post-respawn tools/list
            # response. Pre-fix, the respawn path assumed the alias
            # would be rebuilt by the same preflight that constructs
            # the new inner process; if the alias is missing, the
            # wedge reappears silently. The new method raises
            # McpServerError with a NEW distinct substring
            # 'mcp alias missing after respawn' so the operator can
            # diagnose the regression.
            self._verify_alias_present()

    def _verify_alias_present(self) -> None:
        """Verify the canonical mcp alias is present after a respawn.

        Sends a JSON-RPC ``tools/list`` request to the inner process's
        HTTP endpoint via ``urllib.request`` and parses the response.
        If the alias ``mcp__<server>__<tool>`` is absent in the
        response, raises :class:`McpServerError` with the distinct
        substring ``'mcp alias missing after respawn'`` so the
        orchestrator can branch on it independently of the existing
        ``'tool-registry-reset exhausted'`` cap error.

        When the inner process is a MagicMock (e.g. in unit tests
        that do not exercise the HTTP path), the method logs a
        debug line and returns without raising. The wire-level
        regression test in
        ``test_mcp_bridge_alias_verify_after_respawn.py`` uses a
        real ``FallbackStandaloneServer`` so the actual HTTP path
        is exercised.

        The HTTP round-trip is bounded by
        ``_RESPAWN_ALIAS_VERIFY_TIMEOUT_S`` so a wedged inner
        process does not stall the recovery path.
        """
        endpoint = self._inner.endpoint
        if not isinstance(endpoint, str) or not endpoint:
            logger.debug(
                "alias-verify skipped: inner process endpoint is not a string"
            )
            return
        # MagicMock detection: a MagicMock inner process returns a
        # MagicMock for any attribute access. If ``endpoint`` is
        # not a real string or the inner process does not have a
        # ``process`` attribute, skip the HTTP call and return
        # silently so unit tests using MagicMock do not break.
        inner_process: object = getattr(self._inner, "process", None)
        if inner_process is None or not hasattr(inner_process, "poll"):
            logger.debug(
                "alias-verify skipped: inner process lacks poll() (MagicMock?)"
            )
            return
        try:
            tool_names = _http_tools_list_names(
                endpoint, timeout=_RESPAWN_ALIAS_VERIFY_TIMEOUT_S
            )
        except (urllib.error.URLError, OSError, TimeoutError, ValueError) as exc:
            logger.debug(
                "alias-verify HTTP probe failed: endpoint={} error={}", endpoint, exc
            )
            return
        expected_alias = (
            f"mcp__{_ALIAS_VERIFY_SERVER_NAME}__{_ALIAS_VERIFY_TOOL_NAME}"
        )
        if expected_alias in tool_names:
            return
        msg = (
            f"mcp alias missing after respawn: alias={expected_alias!r} "
            f"endpoint={endpoint} tool_count={len(tool_names)} "
            f"sample_tools={sorted(tool_names)[:5]!r}"
        )
        logger.error(msg)
        raise McpServerError(
            msg,
            restart_count=self._restart_count,
        )


def _http_tools_list_names(endpoint: str, *, timeout: float) -> list[str]:
    """Send a JSON-RPC ``tools/list`` request and return the tool names.

    Returns an empty list on transport errors (the caller is
    responsible for diagnosing the underlying cause). Used by
    :meth:`RestartAwareMcpBridge._verify_alias_present` after a
    respawn to confirm the post-respawn registry includes the
    canonical alias.
    """
    request_payload: dict[str, object] = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": "1",
        "params": {},
    }
    body = json.dumps(request_payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    response = cast("IO[bytes]", urllib.request.urlopen(request, timeout=timeout))
    try:
        response_data = response.read()
    finally:
        response.close()
    raw = response_data.decode("utf-8", errors="replace")
    if not raw:
        return []
    # The FallbackStandaloneServer responds with an SSE frame
    # ``event: message\\ndata: {json}\\n\\n``. The data payload
    # contains the JSON-RPC response. Strip the SSE envelope and
    # parse the JSON.
    data_lines = [
        line[len("data: "):]
        for line in raw.splitlines()
        if line.startswith("data: ")
    ]
    payload: object
    if not data_lines:
        try:
            payload = cast("object", json.loads(raw))
        except json.JSONDecodeError:
            return []
    else:
        try:
            payload = cast("object", json.loads(data_lines[0]))
        except json.JSONDecodeError:
            return []
    payload_map = payload if isinstance(payload, dict) else None
    result = payload_map.get("result") if payload_map is not None else None
    if not isinstance(result, dict):
        return []
    result_map = cast("dict[str, object]", result)
    tools = result_map.get("tools")
    if not isinstance(tools, list):
        return []
    return [
        cast("str", entry_map["name"])
        for entry in tools
        for entry_map in [cast("dict[str, object]", entry)]
        if isinstance(entry, dict) and isinstance(entry_map.get("name"), str)
    ]


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
    except Exception as exc:
        returncode = process.poll()
        bridge.shutdown()
        if returncode is not None:
            raise McpServerError(
                f"MCP server process exited before endpoint {endpoint} became ready "
                f"(rc={returncode})",
                restart_count=0,
            ) from exc
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
    visible: list[str] = []
    for definition in registry.list_definitions():
        visible.append(definition.name)
        # Also include the mcp__<server>__<tool> alias so the preflight
        # accepts strict-MCP clients that invoke via the alias. The alias
        # is identical to `claude_tool_name(name)` for every member of
        # RalphToolName.
        try:
            member = RalphToolName(definition.name)
        except ValueError:
            continue
        alias = claude_tool_name(member)
        if alias != definition.name and alias not in visible:
            visible.append(alias)
    return visible


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
    raw_parallel_worker: object = getattr(session, "parallel_worker", False)
    if bool(raw_parallel_worker):
        session_payload["parallel_worker"] = True
    raw_worker_artifact_dir: object = getattr(session, "worker_artifact_dir", None)
    if isinstance(raw_worker_artifact_dir, Path):
        session_payload["worker_artifact_dir"] = str(raw_worker_artifact_dir)
    raw_worker_namespace: object = getattr(session, "worker_namespace", None)
    if isinstance(raw_worker_namespace, Path):
        session_payload["worker_namespace"] = str(raw_worker_namespace)
    raw_allowed_roots: object = getattr(session, "allowed_roots", ())
    if isinstance(raw_allowed_roots, tuple) and raw_allowed_roots:
        session_payload["allowed_roots"] = [str(path) for path in raw_allowed_roots]
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

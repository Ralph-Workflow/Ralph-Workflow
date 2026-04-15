"""MCP server lifecycle helpers using a standalone localhost HTTP process."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import sys
import tempfile
from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path
from typing import TYPE_CHECKING, Protocol, cast

from ralph.mcp.env import MCP_SESSION_FILE_ENV as SESSION_FILE_ENV
from ralph.mcp.startup import (
    SessionBridgeLike,
    mcp_preflight_timeout_from_env,
    preflight_http_mcp_server_tools,
)
from ralph.mcp.tool_bridge import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.mcp.startup import SessionLike, WorkspaceLike


_PACKAGE_ROOT = Path(__file__).resolve().parents[3]


class ProcessLike(Protocol):
    def poll(self) -> int | None: ...
    def terminate(self) -> None: ...
    def wait(self, timeout: float | None = None) -> int | None: ...
    def kill(self) -> None: ...


type SpawnProcess = Callable[[list[str], Path, dict[str, str]], ProcessLike]
type PreflightFn = Callable[[str, list[str], timedelta], None]


@dataclass(frozen=True)
class LifecycleDeps:
    reserve_port: Callable[[], int]
    create_session_file: Callable[[Path, SessionLike], Path]
    subprocess_env: Callable[[Path], dict[str, str]]
    spawn_process: SpawnProcess
    preflight: PreflightFn
    preflight_timeout: Callable[[], timedelta]


@dataclass
class StandaloneMcpProcess:
    endpoint: str
    process: ProcessLike
    session_file: Path

    def start(self) -> None:
        return

    def agent_endpoint_uri(self) -> str:
        return self.endpoint

    def endpoint_uri(self) -> str:
        return self.endpoint

    def shutdown(self) -> None:
        if self.process.poll() is not None:
            return
        self.process.terminate()
        try:
            self.process.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self.process.kill()
            self.process.wait(timeout=5)


def start_mcp_server(
    session: SessionLike,
    workspace: WorkspaceLike,
    *,
    deps: LifecycleDeps | None = None,
) -> SessionBridgeLike:
    """Start a standalone Ralph MCP HTTP subprocess and verify tool reachability."""
    lifecycle_deps = deps or _default_lifecycle_deps()
    root = _workspace_root(workspace)
    port = lifecycle_deps.reserve_port()
    endpoint = f"http://127.0.0.1:{port}/mcp"
    session_file = lifecycle_deps.create_session_file(root, session)
    env = lifecycle_deps.subprocess_env(session_file)
    process = lifecycle_deps.spawn_process(
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
    )
    bridge = StandaloneMcpProcess(endpoint=endpoint, process=process, session_file=session_file)

    try:
        lifecycle_deps.preflight(
            endpoint,
            _visible_mcp_tool_names_owned(session, workspace),
            lifecycle_deps.preflight_timeout(),
        )
    except Exception:
        bridge.shutdown()
        raise

    return cast("SessionBridgeLike", bridge)


def shutdown_mcp_server(bridge: SessionBridgeLike) -> None:
    """Shutdown MCP server process."""
    bridge.shutdown()


def _visible_mcp_tool_names_owned(session: SessionLike, workspace: WorkspaceLike) -> list[str]:
    registry = build_ralph_tool_registry(session, workspace)
    return [definition.name for definition in registry.list_definitions()]


def _workspace_root(workspace: WorkspaceLike) -> Path:
    if isinstance(workspace, FsWorkspace):
        return workspace._root
    return Path.cwd()


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


def _spawn_process(command: list[str], cwd: Path, env: dict[str, str]) -> subprocess.Popen[str]:
    return subprocess.Popen(
        command,
        cwd=str(cwd),
        env=env,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )


def _default_lifecycle_deps() -> LifecycleDeps:
    return LifecycleDeps(
        reserve_port=_reserve_port,
        create_session_file=_create_session_file,
        subprocess_env=_subprocess_env,
        spawn_process=_spawn_process,
        preflight=preflight_http_mcp_server_tools,
        preflight_timeout=mcp_preflight_timeout_from_env,
    )


def _create_session_file(root: Path, session: SessionLike) -> Path:
    session_dir = root / ".agent" / "tmp"
    session_dir.mkdir(parents=True, exist_ok=True)
    fd, temp_path = tempfile.mkstemp(prefix="ralph-mcp-session-", suffix=".json", dir=session_dir)
    os.close(fd)
    path = Path(temp_path)
    path.write_text(_session_payload_json(session), encoding="utf-8")
    return path


def _session_payload_json(session: SessionLike) -> str:
    session_payload: dict[str, object] = {
        "session_id": session.session_id,
        "run_id": session.run_id,
        "drain": session.drain,
        "capabilities": sorted(session.capabilities),
    }
    return json.dumps(session_payload)


__all__ = [
    "LifecycleDeps",
    "SessionBridgeLike",
    "shutdown_mcp_server",
    "start_mcp_server",
]

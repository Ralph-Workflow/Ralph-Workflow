"""MCP server lifecycle helpers using a standalone localhost HTTP process."""

from __future__ import annotations

import json
import os
import socket
import subprocess
import tempfile
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.server.runtime import SESSION_FILE_ENV
from ralph.mcp.startup import (
    SessionBridgeLike,
    mcp_preflight_timeout_from_env,
    preflight_http_mcp_server_tools,
)
from ralph.mcp.tool_bridge import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.mcp.startup import SessionBridgeFactory, SessionLike, WorkspaceLike


@dataclass
class StandaloneMcpProcess:
    endpoint: str
    process: subprocess.Popen[str]
    session_file: Path

    def start(self) -> None:
        return

    def agent_endpoint_uri(self) -> str:
        return self.endpoint

    def endpoint_uri(self) -> str:
        return self.endpoint

    def update_session(self, session: SessionLike) -> None:
        self.session_file.write_text(_session_payload_json(session), encoding="utf-8")

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
    bridge_factory: SessionBridgeFactory | None = None,
) -> SessionBridgeLike:
    """Start a standalone Ralph MCP HTTP subprocess and verify tool reachability."""
    _ = bridge_factory
    root = _workspace_root(workspace)
    port = _reserve_port()
    endpoint = f"http://127.0.0.1:{port}/mcp"
    session_file = _create_session_file(root, session)
    env = _subprocess_env(session_file)
    process = subprocess.Popen(
        [
            "ralph-mcp",
            "--host",
            "127.0.0.1",
            "--port",
            str(port),
            "--workspace",
            str(root),
        ],
        cwd=str(root),
        env=env,
        text=True,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    bridge = StandaloneMcpProcess(endpoint=endpoint, process=process, session_file=session_file)

    try:
        preflight_http_mcp_server_tools(
            endpoint,
            _visible_mcp_tool_names_owned(session, workspace),
            mcp_preflight_timeout_from_env(),
        )
    except Exception:
        bridge.shutdown()
        raise

    return cast("SessionBridgeLike", bridge)


def shutdown_mcp_server(bridge: SessionBridgeLike) -> None:
    """Shutdown MCP server process."""
    bridge.shutdown()


def configure_mcp_server_session(bridge: SessionBridgeLike, session: SessionLike) -> None:
    """Update the active session metadata for a run-scoped MCP server."""
    if isinstance(bridge, StandaloneMcpProcess):
        bridge.update_session(session)


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
    return env


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
    "SessionBridgeLike",
    "configure_mcp_server_session",
    "shutdown_mcp_server",
    "start_mcp_server",
]

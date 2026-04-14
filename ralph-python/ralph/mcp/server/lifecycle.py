"""MCP server lifecycle helpers using a standalone localhost HTTP process."""

from __future__ import annotations

import json
import os
import socket
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, cast

from ralph.mcp.server.runtime import SESSION_ENV
from ralph.mcp.startup import (
    SessionBridgeLike,
    mcp_preflight_timeout_from_env,
    preflight_http_mcp_server_tools,
)
from ralph.mcp.tool_bridge import build_ralph_tool_registry

if TYPE_CHECKING:
    from ralph.mcp.startup import SessionBridgeFactory, SessionLike, WorkspaceLike


@dataclass
class StandaloneMcpProcess:
    endpoint: str
    process: subprocess.Popen[str]

    def agent_endpoint_uri(self) -> str:
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
    bridge_factory: SessionBridgeFactory | None = None,
) -> SessionBridgeLike:
    """Start a standalone Ralph MCP HTTP subprocess and verify tool reachability."""
    _ = bridge_factory
    root = _workspace_root(workspace)
    port = _reserve_port()
    endpoint = f"http://127.0.0.1:{port}/mcp"
    env = _subprocess_env(session)
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
    bridge = StandaloneMcpProcess(endpoint=endpoint, process=process)

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


def _visible_mcp_tool_names_owned(session: SessionLike, workspace: WorkspaceLike) -> list[str]:
    registry = build_ralph_tool_registry(session, workspace)
    return [definition.name for definition in registry.list_definitions()]


def _workspace_root(workspace: WorkspaceLike) -> Path:
    root = getattr(workspace, "_root", None)
    if isinstance(root, Path):
        return root
    if isinstance(root, str):
        return Path(root)
    return Path.cwd()


def _reserve_port() -> int:
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        return cast("int", sock.getsockname()[1])


def _subprocess_env(session: SessionLike) -> dict[str, str]:
    env = dict(os.environ)
    session_id = cast("str", getattr(session, "session_id"))
    run_id = cast("str", getattr(session, "run_id"))
    drain = cast("str", getattr(session, "drain"))
    capabilities = cast("set[str]", getattr(session, "capabilities", set()))
    env[SESSION_ENV] = json.dumps(
        {
            "session_id": session_id,
            "run_id": run_id,
            "drain": drain,
            "capabilities": sorted(capabilities),
        }
    )
    return env


__all__ = ["SessionBridgeLike", "shutdown_mcp_server", "start_mcp_server"]

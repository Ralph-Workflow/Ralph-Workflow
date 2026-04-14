"""Integration tests for the Python MCP server bridge."""

from __future__ import annotations

import os
from datetime import timedelta
from pathlib import Path
from typing import Any

from ralph.mcp import session_bridge, startup
from ralph.workspace import Workspace
from ralph.workspace.fs import FsWorkspace


class _WorkspaceRoot:
    def __init__(self, root: Path) -> None:
        self.root = root
        self._workspace = FsWorkspace(root)

    def read(self, path: str) -> str:
        return self._workspace.read(path)

    def write(self, path: str, content: str) -> None:
        self._workspace.write(path, content)

    def append(self, path: str, content: str) -> None:
        self._workspace.append(path, content)

    def exists(self, path: str) -> bool:
        return self._workspace.exists(path)

    def remove(self, path: str) -> None:
        self._workspace.remove(path)

    def list_dir(self, path: str) -> list[str]:
        return self._workspace.list_dir(path)

    def is_dir(self, path: str) -> bool:
        return self._workspace.is_dir(path)

    def absolute_path(self, path: str) -> str:
        return str(self.root / path)

    def is_file(self, path: str) -> bool:
        return (self.root / path).is_file()


def _bridge_factory(
    session: startup.SessionLike,
    current_workspace: startup.WorkspaceLike,
) -> startup.SessionBridgeLike:
    assert isinstance(session, session_bridge.AgentSession)
    assert isinstance(current_workspace, Workspace)
    return session_bridge.SessionBridge(session, current_workspace)


def _session(run_id: str = "run-1") -> session_bridge.AgentSession:
    return session_bridge.AgentSession(
        session_id=f"session-{run_id}",
        run_id=run_id,
        drain="development",
        capabilities={
            "RunReportProgress",
            "ArtifactSubmit",
            "EnvRead",
            "WorkspaceRead",
        },
    )


def _http_call(endpoint: str, method: str, params: dict[str, Any] | None = None, *, msg_id: int = 1) -> dict[str, Any]:
    target = startup.parse_http_endpoint(endpoint)
    return startup.post_http_jsonrpc(
        target,
        {
            "jsonrpc": "2.0",
            "id": msg_id,
            "method": method,
            "params": params or {},
        },
    )


def test_start_mcp_server_for_session_preflights_registered_tools(tmp_path: Path) -> None:
    workspace = _WorkspaceRoot(tmp_path)
    bridge = startup.start_mcp_server_for_session(
        _session(),
        workspace,
        bridge_factory=_bridge_factory,
    )

    try:
        assert bridge.agent_endpoint_uri().startswith("http://127.0.0.1:")
        lease_path = session_bridge.endpoint_lease_path(tmp_path)
        assert lease_path.exists()

        startup.preflight_http_mcp_server_tools(
            bridge.agent_endpoint_uri(),
            ["report_progress", "coordinate", "read_env"],
            timedelta(seconds=1),
        )
    finally:
        bridge.shutdown()


def test_session_bridge_http_gateway_lists_and_calls_coordination_tools(
    tmp_path: Path, monkeypatch
) -> None:
    monkeypatch.setenv("RALPH_MCP_TEST_ENV", "expected-value")

    bridge = session_bridge.SessionBridge(_session(), _WorkspaceRoot(tmp_path))
    bridge.start()

    try:
        initialize_response = _http_call(bridge.agent_endpoint_uri(), "initialize")
        assert initialize_response["result"]["serverInfo"]["name"] == "ralph-mcp"

        tools_response = _http_call(bridge.agent_endpoint_uri(), "tools/list", msg_id=2)
        tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}
        assert {"report_progress", "coordinate", "read_env"}.issubset(tool_names)

        progress_response = _http_call(
            bridge.agent_endpoint_uri(),
            "tools/call",
            {
                "name": "report_progress",
                "arguments": {"status": "running", "note": "halfway"},
            },
            msg_id=3,
        )
        progress_text = progress_response["result"]["content"][0]["text"]
        assert "status='running'" in progress_text
        assert "note='halfway'" in progress_text

        coordinate_response = _http_call(
            bridge.agent_endpoint_uri(),
            "tools/call",
            {
                "name": "coordinate",
                "arguments": {
                    "action": "claim",
                    "work_unit_id": "WU-7",
                    "payload": {"lane": "bridge"},
                },
            },
            msg_id=4,
        )
        coordinate_text = coordinate_response["result"]["content"][0]["text"]
        assert "Coordination action 'claim' processed" in coordinate_text
        assert "work_unit_id=WU-7" in coordinate_text
        assert 'payload={"lane": "bridge"}' in coordinate_text

        env_response = _http_call(
            bridge.agent_endpoint_uri(),
            "tools/call",
            {"name": "read_env", "arguments": {"name": "RALPH_MCP_TEST_ENV"}},
            msg_id=5,
        )
        assert env_response["result"]["content"][0]["text"] == "RALPH_MCP_TEST_ENV=expected-value"
    finally:
        bridge.shutdown()


def test_session_bridge_reuses_lease_file_to_increment_generation(tmp_path: Path) -> None:
    first_bridge = session_bridge.SessionBridge(_session("shared-run"), _WorkspaceRoot(tmp_path))
    first_bridge.start()

    try:
        first_lease = first_bridge.endpoint_lease()
        assert first_lease is not None
        assert first_lease.generation == 1
    finally:
        first_bridge.shutdown()

    second_bridge = session_bridge.SessionBridge(_session("shared-run"), _WorkspaceRoot(tmp_path))
    second_bridge.start()

    try:
        second_lease = second_bridge.endpoint_lease()
        assert second_lease is not None
        assert second_lease.generation == 2
        assert os.environ.get(session_bridge.MCP_ENDPOINT_ENV) is None
    finally:
        second_bridge.shutdown()

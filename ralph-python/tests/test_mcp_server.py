"""Integration tests for the Python MCP server bridge."""

from __future__ import annotations

import asyncio
import json
import os
import socket
import threading
from datetime import timedelta
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock

from ralph.mcp import session_bridge, startup
from ralph.mcp.server import lifecycle
from ralph.mcp.server import runtime as server_runtime
from ralph.mcp.session_bridge import AgentSession
from ralph.workspace import Workspace
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

SECOND_GENERATION = 2


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


def _session(
    run_id: str = "run-1", capabilities: set[str] | None = None
) -> session_bridge.AgentSession:
    return session_bridge.AgentSession(
        session_id=f"session-{run_id}",
        run_id=run_id,
        drain="development",
        capabilities=capabilities
        or {
            "RunReportProgress",
            "ArtifactSubmit",
            "EnvRead",
            "WorkspaceRead",
        },
    )


def _http_call(
    endpoint: str, method: str, params: dict[str, Any] | None = None, *, msg_id: int = 1
) -> dict[str, Any]:
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


def test_file_backed_session_allows_workspace_write_any_via_ephemeral_alias(
    tmp_path: Path,
) -> None:
    session_file = tmp_path / "session.json"
    session_file.write_text(
        json.dumps(
            {
                "session_id": "commit-session",
                "run_id": "run-commit",
                "drain": "commit",
                "capabilities": ["WorkspaceWriteEphemeral"],
            }
        ),
        encoding="utf-8",
    )

    session = server_runtime.FileBackedSession(session_file)

    assert session.check_capability("WorkspaceWriteAny") == "approved"


def test_build_fastmcp_server_falls_back_without_mcp_dependency(
    tmp_path: Path, monkeypatch
) -> None:
    session = _session(capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress"})

    monkeypatch.setattr(server_runtime, "_FastMCP", None)
    monkeypatch.setattr(server_runtime, "_Tool", None)

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = cast("int", sock.getsockname()[1])

    server = server_runtime.build_fastmcp_server(
        tmp_path, host="127.0.0.1", port=port, session=session
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"transport": server_runtime.DEFAULT_TRANSPORT},
        daemon=True,
    )
    thread.start()

    endpoint = f"http://127.0.0.1:{port}/mcp"
    try:
        initialize_response = _http_call(endpoint, "initialize")
        assert initialize_response["result"]["serverInfo"]["name"] == "ralph-mcp"

        tools_response = _http_call(endpoint, "tools/list", msg_id=2)
        tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}
        assert {"read_file", "report_progress", "coordinate"}.issubset(tool_names)
    finally:
        cast("server_runtime._FallbackStandaloneServer", server)._httpd.shutdown()  # type: ignore[attr-defined]
        cast("server_runtime._FallbackStandaloneServer", server)._httpd.server_close()  # type: ignore[attr-defined]
        thread.join(timeout=1)


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


def test_session_bridge_http_gateway_hides_tools_without_capability(tmp_path: Path) -> None:
    bridge = session_bridge.SessionBridge(_session(), _WorkspaceRoot(tmp_path))
    bridge.start()

    try:
        _http_call(bridge.agent_endpoint_uri(), "initialize")
        tools_response = _http_call(bridge.agent_endpoint_uri(), "tools/list", msg_id=40)
        tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}

        assert "read_file" in tool_names
        assert "report_progress" in tool_names
        assert "exec" not in tool_names
        assert "write_file" not in tool_names
        assert "git_diff" not in tool_names
    finally:
        bridge.shutdown()


def test_session_bridge_http_gateway_rejects_tool_call_without_capability(tmp_path: Path) -> None:
    bridge = session_bridge.SessionBridge(_session(), _WorkspaceRoot(tmp_path))
    bridge.start()

    try:
        _http_call(bridge.agent_endpoint_uri(), "initialize")
        response = _http_call(
            bridge.agent_endpoint_uri(),
            "tools/call",
            {"name": "exec", "arguments": {"command": "pwd"}},
            msg_id=41,
        )

        assert response["result"] is None
        assert "Unknown tool: exec" in response["error"]["message"]
    finally:
        bridge.shutdown()


def test_build_fastmcp_server_filters_tools_by_session_capabilities(tmp_path: Path) -> None:
    session = AgentSession(
        session_id="session-filtered",
        run_id="run-filtered",
        drain="planning",
        capabilities={"WorkspaceRead", "ArtifactSubmit"},
    )

    server = server_runtime.build_fastmcp_server(tmp_path, session=session)
    tool_names = {tool.name for tool in server._tool_manager.list_tools()}

    assert "read_file" in tool_names
    assert "ralph_submit_artifact" in tool_names
    assert "exec" not in tool_names
    assert "write_file" not in tool_names


def test_session_bridge_http_gateway_accepts_structured_commit_artifact(tmp_path: Path) -> None:
    bridge = session_bridge.SessionBridge(_session(), _WorkspaceRoot(tmp_path))
    bridge.start()

    try:
        initialize_response = _http_call(bridge.agent_endpoint_uri(), "initialize")
        assert initialize_response["result"]["serverInfo"]["name"] == "ralph-mcp"

        tools_response = _http_call(bridge.agent_endpoint_uri(), "tools/list", msg_id=30)
        tool_names = {tool["name"] for tool in tools_response["result"]["tools"]}
        assert "ralph_submit_artifact" in tool_names

        submit_response = _http_call(
            bridge.agent_endpoint_uri(),
            "tools/call",
            {
                "name": "ralph_submit_artifact",
                "arguments": {
                    "artifact_type": "commit_message",
                    "content": json.dumps(
                        {
                            "type": "commit",
                            "subject": "fix(api): normalize payload validation",
                            "body": "Keep MCP artifacts aligned with the formal schema.",
                        }
                    ),
                },
            },
            msg_id=31,
        )
        assert submit_response["result"]["isError"] is False
        assert (
            submit_response["result"]["content"][0]["text"] == "Artifact submitted: commit_message"
        )

        artifact_file = tmp_path / ".agent" / "tmp" / "commit_message.json"
        stored = json.loads(artifact_file.read_text(encoding="utf-8"))
        assert stored["content"] == {
            "type": "commit",
            "subject": "fix(api): normalize payload validation",
            "body": "Keep MCP artifacts aligned with the formal schema.",
        }
    finally:
        bridge.shutdown()


def test_session_bridge_http_gateway_rejects_legacy_commit_artifact_payload(tmp_path: Path) -> None:
    bridge = session_bridge.SessionBridge(_session(), _WorkspaceRoot(tmp_path))
    bridge.start()

    try:
        _http_call(bridge.agent_endpoint_uri(), "initialize")
        legacy_response = _http_call(
            bridge.agent_endpoint_uri(),
            "tools/call",
            {
                "name": "ralph_submit_artifact",
                "arguments": {
                    "artifact_type": "commit_message",
                    "content": json.dumps({"message": "fix: legacy format"}),
                },
            },
            msg_id=32,
        )
        assert legacy_response["result"] is None
        assert "structured commit_message schema" in legacy_response["error"]["message"]
    finally:
        bridge.shutdown()


def test_session_bridge_http_gateway_accepts_structured_plan_artifact(tmp_path: Path) -> None:
    bridge = session_bridge.SessionBridge(_session(), _WorkspaceRoot(tmp_path))
    bridge.start()

    try:
        _http_call(bridge.agent_endpoint_uri(), "initialize")
        response = _http_call(
            bridge.agent_endpoint_uri(),
            "tools/call",
            {
                "name": "ralph_submit_artifact",
                "arguments": {
                    "artifact_type": "plan",
                    "content": json.dumps(
                        {
                            "summary": {
                                "context": "Plan MCP validation hardening.",
                                "scope_items": [
                                    {"text": "Update validator"},
                                    {"text": "Add tests"},
                                    {"text": "Adjust prompt"},
                                ],
                            },
                            "steps": [
                                {
                                    "number": 1,
                                    "step_type": "file_change",
                                    "title": "Validate plans",
                                    "content": "Enforce plan schema in the MCP server.",
                                }
                            ],
                            "critical_files": {
                                "primary_files": [
                                    {"path": "ralph/mcp/tool_artifact.py", "action": "modify"}
                                ]
                            },
                            "risks_mitigations": [
                                {"risk": "Schema drift", "mitigation": "Add HTTP tests"}
                            ],
                            "verification_strategy": [
                                {"method": "pytest", "expected_outcome": "green"}
                            ],
                        }
                    ),
                },
            },
            msg_id=33,
        )
        assert response["result"]["isError"] is False
        stored = json.loads(
            (tmp_path / ".agent" / "artifacts" / "plan.json").read_text(encoding="utf-8")
        )
        assert stored["content"]["summary"]["context"] == "Plan MCP validation hardening."
    finally:
        bridge.shutdown()


def test_session_bridge_http_gateway_rejects_invalid_development_result(tmp_path: Path) -> None:
    bridge = session_bridge.SessionBridge(_session(), _WorkspaceRoot(tmp_path))
    bridge.start()

    try:
        _http_call(bridge.agent_endpoint_uri(), "initialize")
        response = _http_call(
            bridge.agent_endpoint_uri(),
            "tools/call",
            {
                "name": "ralph_submit_artifact",
                "arguments": {
                    "artifact_type": "development_result",
                    "content": json.dumps(
                        {
                            "status": "partial",
                            "summary": "Half complete.",
                            "files_changed": "- src/example.py",
                        }
                    ),
                },
            },
            msg_id=35,
        )
        assert response["result"] is None
        assert "next_steps" in response["error"]["message"]
    finally:
        bridge.shutdown()


def test_session_bridge_http_gateway_rejects_malformed_plan_artifact(tmp_path: Path) -> None:
    bridge = session_bridge.SessionBridge(_session(), _WorkspaceRoot(tmp_path))
    bridge.start()

    try:
        _http_call(bridge.agent_endpoint_uri(), "initialize")
        response = _http_call(
            bridge.agent_endpoint_uri(),
            "tools/call",
            {
                "name": "ralph_submit_artifact",
                "arguments": {
                    "artifact_type": "plan",
                    "content": json.dumps(
                        {
                            "summary": {
                                "context": "Too small.",
                                "scope_items": [{"text": "Only one"}],
                            },
                            "steps": [
                                {
                                    "number": 1,
                                    "title": "Bad",
                                    "content": "Missing required sections",
                                }
                            ],
                        }
                    ),
                },
            },
            msg_id=34,
        )
        assert response["result"] is None
        assert (
            "verification_strategy" in response["error"]["message"]
            or "scope_items" in response["error"]["message"]
        )
    finally:
        bridge.shutdown()


def test_session_bridge_reuses_lease_file_to_increment_generation(tmp_path: Path) -> None:
    first_bridge = session_bridge.SessionBridge(_session("shared-run"), _WorkspaceRoot(tmp_path))
    first_bridge.start()

    try:
        first_lease = first_bridge.endpoint_lease()
        assert first_lease is not None
        assert first_lease.generation == 1
        assert first_lease.endpoint.startswith("http://127.0.0.1:")
    finally:
        first_bridge.shutdown()

    second_bridge = session_bridge.SessionBridge(_session("shared-run"), _WorkspaceRoot(tmp_path))
    second_bridge.start()

    try:
        second_lease = second_bridge.endpoint_lease()
        assert second_lease is not None
        assert second_lease.generation == SECOND_GENERATION
        assert second_lease.endpoint.startswith("http://127.0.0.1:")
        assert os.environ.get(session_bridge.MCP_ENDPOINT_ENV) is None
    finally:
        second_bridge.shutdown()


def test_tools_list_preserves_registry_input_schema(tmp_path: Path) -> None:
    bridge = session_bridge.SessionBridge(_session(), _WorkspaceRoot(tmp_path))
    bridge.start()

    try:
        tools_response = _http_call(bridge.agent_endpoint_uri(), "tools/list", msg_id=20)
        tools = {tool["name"]: tool for tool in tools_response["result"]["tools"]}
        read_env_schema = tools["read_env"]["inputSchema"]
        assert read_env_schema["required"] == ["name"]
        assert "name" in read_env_schema["properties"]
    finally:
        bridge.shutdown()


def test_tools_call_exec_failure_preserves_is_error_flag(tmp_path: Path) -> None:
    bridge = session_bridge.SessionBridge(
        _session(capabilities={"ProcessExecBounded", "WorkspaceRead"}),
        _WorkspaceRoot(tmp_path),
    )
    bridge.start()

    try:
        exec_response = _http_call(
            bridge.agent_endpoint_uri(),
            "tools/call",
            {
                "name": "exec",
                "arguments": {
                    "command": "false",
                },
            },
            msg_id=21,
        )
        assert exec_response["result"]["isError"] is True
    finally:
        bridge.shutdown()


def test_build_fastmcp_server_preserves_registry_input_schema(tmp_path: Path) -> None:
    server = server_runtime.build_fastmcp_server(tmp_path)

    tool_manager = server._tool_manager
    tools = {tool.name: tool for tool in tool_manager.list_tools()}

    read_env_schema = cast("dict[str, object]", tools["read_env"].parameters)
    properties = cast("dict[str, object]", read_env_schema["properties"])
    assert read_env_schema["required"] == ["name"]
    assert "name" in properties

    submit_artifact_schema = cast("dict[str, object]", tools["ralph_submit_artifact"].parameters)
    submit_properties = cast("dict[str, object]", submit_artifact_schema["properties"])
    assert "partial" not in submit_properties


def test_runtime_main_launches_streamable_http_server(monkeypatch, tmp_path: Path) -> None:
    monkeypatch.chdir(tmp_path)
    observed: dict[str, object] = {}

    def fake_run_standalone_server(
        workspace_root: Path,
        *,
        transport: str,
        host: str,
        port: int,
    ) -> None:
        observed.update(
            {
                "workspace_root": workspace_root,
                "transport": transport,
                "host": host,
                "port": port,
            }
        )

    monkeypatch.setattr(server_runtime, "run_standalone_server", fake_run_standalone_server)

    server_runtime.main(["--host", "0.0.0.0", "--port", "8123"])

    assert observed == {
        "workspace_root": tmp_path,
        "transport": "streamable-http",
        "host": "0.0.0.0",
        "port": 8123,
    }


def test_build_fastmcp_server_normalizes_tool_result_payload(tmp_path: Path) -> None:
    server = server_runtime.build_fastmcp_server(tmp_path)

    result = cast(
        "dict[str, object]",
        asyncio.run(server._tool_manager.call_tool("report_progress", {"status": "running"})),
    )

    assert isinstance(result, dict)
    assert result["isError"] is False
    assert isinstance(result["content"], list)


def test_configure_mcp_server_session_updates_session_file(tmp_path: Path) -> None:
    session_file = tmp_path / "session.json"
    session_file.write_text(
        json.dumps(
            {
                "session_id": "old",
                "run_id": "old-run",
                "drain": "planning",
                "capabilities": ["WorkspaceRead"],
            }
        ),
        encoding="utf-8",
    )
    bridge = lifecycle.StandaloneMcpProcess(
        endpoint="http://127.0.0.1:8765/mcp",
        process=MagicMock(),
        session_file=session_file,
    )
    session = AgentSession(
        session_id="new-session",
        run_id="new-run",
        drain="development",
        capabilities={"ArtifactSubmit", "WorkspaceRead"},
    )

    lifecycle.configure_mcp_server_session(bridge, session)

    payload = json.loads(session_file.read_text(encoding="utf-8"))
    assert payload["session_id"] == "new-session"
    assert payload["run_id"] == "new-run"
    assert payload["drain"] == "development"
    assert sorted(payload["capabilities"]) == ["ArtifactSubmit", "WorkspaceRead"]

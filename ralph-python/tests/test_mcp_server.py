"""Integration tests for the standalone Python MCP server runtime."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from typing import TYPE_CHECKING, Any, cast

from ralph.mcp import startup
from ralph.mcp.server import runtime as server_runtime
from ralph.mcp.session import AgentSession

if TYPE_CHECKING:
    from pathlib import Path


def _session(run_id: str = "run-1", capabilities: set[str] | None = None) -> AgentSession:
    return AgentSession(
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

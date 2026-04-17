"""Integration tests for the standalone Python MCP server runtime."""

from __future__ import annotations

import asyncio
import json
import socket
import threading
from pathlib import Path
from typing import Any, cast

import httpx

from ralph.mcp import startup
from ralph.mcp.server import runtime as server_runtime
from ralph.mcp.session import AgentSession
from ralph.phases import PhaseContext
from ralph.phases.planning import handle_planning
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.workspace.fs import FsWorkspace

HTTP_OK = 200
HTTP_ACCEPTED = 202


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


def test_session_from_env_mapping_supports_json_payload() -> None:
    session = server_runtime.session_from_env(
        {
            "RALPH_MCP_SESSION_JSON": json.dumps(
                {
                    "session_id": "json-session",
                    "run_id": "json-run",
                    "drain": "planning",
                    "capabilities": ["WorkspaceRead", "ArtifactSubmit"],
                }
            )
        }
    )

    assert session is not None
    assert session.session_id == "json-session"
    assert session.capabilities == {"WorkspaceRead", "ArtifactSubmit"}


def test_session_from_env_accepts_injected_id_factories() -> None:
    session = server_runtime.session_from_env(
        {},
        session_id_factory=lambda: "generated-session",
        run_id_factory=lambda: "generated-run",
    )

    assert session is None


def test_file_backed_session_accepts_injected_loader() -> None:
    session = server_runtime.FileBackedSession(
        Path("/unused/session.json"),
        loader=lambda _path: {
            "session_id": "loader-session",
            "run_id": "loader-run",
            "drain": "planning",
            "capabilities": ["WorkspaceRead"],
        },
    )

    assert session.session_id == "loader-session"
    assert session.run_id == "loader-run"
    assert session.capabilities == {"WorkspaceRead"}


def test_file_backed_session_accepts_injected_fallback_id_factories() -> None:
    session = server_runtime.FileBackedSession(
        Path("/unused/session.json"),
        loader=lambda _path: {},
        session_id_factory=lambda: "fallback-session",
        run_id_factory=lambda: "fallback-run",
    )

    assert session.session_id == "fallback-session"
    assert session.run_id == "fallback-run"


def test_build_fastmcp_server_falls_back_without_mcp_dependency(
    tmp_path: Path, monkeypatch
) -> None:
    session = _session(capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress"})

    monkeypatch.setattr(server_runtime, "_FastMCP", None)
    monkeypatch.setattr(server_runtime, "_Tool", None)
    server = server_runtime.build_fastmcp_server(tmp_path, session=session)

    assert isinstance(server, server_runtime._FallbackStandaloneServer)

    mcp_server = server._mcp_server
    state = server_runtime.ServerState.UNINITIALIZED

    initialize_response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="initialize", msg_id=1),
        state,
    )
    assert initialize_response is not None
    initialize_result = cast("dict[str, object]", initialize_response.result)
    assert cast("dict[str, object]", initialize_result["serverInfo"])["name"] == "ralph-mcp"
    assert cast("dict[str, object]", initialize_result["serverInfo"])["version"]
    assert cast("dict[str, object]", initialize_result["capabilities"])["prompts"] == {
        "listChanged": False
    }
    assert cast("dict[str, object]", initialize_result["capabilities"])["resources"] == {
        "subscribe": False,
        "listChanged": False,
    }

    prompts_response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="prompts/list", msg_id=2),
        state,
    )
    resources_response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="resources/list", msg_id=3),
        state,
    )
    templates_response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="resources/templates/list", msg_id=4),
        state,
    )
    assert prompts_response is not None
    assert resources_response is not None
    assert templates_response is not None
    assert prompts_response.result == {"prompts": []}
    assert resources_response.result == {"resources": []}
    assert templates_response.result == {"resourceTemplates": []}

    tools_response, _ = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=5),
        state,
    )
    assert tools_response is not None
    tools_result = cast("dict[str, object]", tools_response.result)
    tool_names = {tool["name"] for tool in cast("list[dict[str, object]]", tools_result["tools"])}
    assert {"read_file", "report_progress", "coordinate"}.issubset(tool_names)


def test_build_standalone_http_server_get_probe_avoids_missing_session_id_error(
    tmp_path: Path,
) -> None:
    session = _session(capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress"})

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = cast("int", sock.getsockname()[1])

    server = server_runtime.build_standalone_http_server(
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
        with httpx.stream(
            "GET",
            endpoint,
            headers={"Accept": "application/json, text/event-stream"},
            timeout=5.0,
        ) as response:
            first_chunk = next(response.iter_text())

            assert response.status_code == HTTP_OK
            assert response.headers["content-type"].startswith("text/event-stream")
            assert "Missing session ID" not in first_chunk
    finally:
        cast("server_runtime._StandaloneHttpServer", server)._httpd.shutdown()  # type: ignore[attr-defined]
        cast("server_runtime._StandaloneHttpServer", server)._httpd.server_close()  # type: ignore[attr-defined]
        thread.join(timeout=1)


def test_build_standalone_http_server_initialized_notification_returns_202(
    tmp_path: Path,
) -> None:
    session = _session(capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress"})

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = cast("int", sock.getsockname()[1])

    server = server_runtime.build_standalone_http_server(
        tmp_path, host="127.0.0.1", port=port, session=session
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"transport": server_runtime.DEFAULT_TRANSPORT},
        daemon=True,
    )
    thread.start()

    endpoint = f"http://127.0.0.1:{port}/mcp"
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    try:
        initialize = httpx.post(
            endpoint,
            headers=headers,
            json=startup.initialize_request(),
            timeout=5.0,
        )
        session_id = initialize.headers["mcp-session-id"]
        initialized = httpx.post(
            endpoint,
            headers={**headers, "mcp-session-id": session_id},
            json=startup.initialized_notification(),
            timeout=5.0,
        )

        assert initialized.status_code == HTTP_ACCEPTED
        assert initialized.text == ""
    finally:
        cast("server_runtime._StandaloneHttpServer", server)._httpd.shutdown()  # type: ignore[attr-defined]
        cast("server_runtime._StandaloneHttpServer", server)._httpd.server_close()  # type: ignore[attr-defined]
        thread.join(timeout=1)


def test_build_standalone_http_server_initialize_sse_omits_null_error_field(
    tmp_path: Path,
) -> None:
    session = _session(capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress"})

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = cast("int", sock.getsockname()[1])

    server = server_runtime.build_standalone_http_server(
        tmp_path, host="127.0.0.1", port=port, session=session
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"transport": server_runtime.DEFAULT_TRANSPORT},
        daemon=True,
    )
    thread.start()

    endpoint = f"http://127.0.0.1:{port}/mcp"
    headers = {"Accept": "application/json, text/event-stream", "Content-Type": "application/json"}
    try:
        response = httpx.post(
            endpoint,
            headers=headers,
            json=startup.initialize_request(),
            timeout=5.0,
        )

        assert response.headers.get("connection") == "keep-alive"
        assert '"error": null' not in response.text
    finally:
        cast("server_runtime._StandaloneHttpServer", server)._httpd.shutdown()  # type: ignore[attr-defined]
        cast("server_runtime._StandaloneHttpServer", server)._httpd.server_close()  # type: ignore[attr-defined]
        thread.join(timeout=1)


def test_build_standalone_http_server_allows_post_while_get_stream_is_open(
    tmp_path: Path,
) -> None:
    session = _session(capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress"})

    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        port = cast("int", sock.getsockname()[1])

    server = server_runtime.build_standalone_http_server(
        tmp_path, host="127.0.0.1", port=port, session=session
    )
    thread = threading.Thread(
        target=server.run,
        kwargs={"transport": server_runtime.DEFAULT_TRANSPORT},
        daemon=True,
    )
    thread.start()

    endpoint = f"http://127.0.0.1:{port}/mcp"
    stream_socket = socket.create_connection(("127.0.0.1", port), timeout=5.0)
    get_request = (
        "GET /mcp HTTP/1.1\r\n"
        f"Host: 127.0.0.1:{port}\r\n"
        "Accept: text/event-stream\r\n"
        f"mcp-session-id: {session.session_id}\r\n"
        "\r\n"
    )
    stream_socket.sendall(get_request.encode())
    try:
        stream_socket.settimeout(5.0)
        received = ""
        while "event: open" not in received:
            chunk = stream_socket.recv(256).decode()
            if not chunk:
                break
            received += chunk
        assert "event: open" in received
        initialize = httpx.post(
            endpoint,
            headers={
                "Accept": "application/json, text/event-stream",
                "Content-Type": "application/json",
            },
            json=startup.initialize_request(),
            timeout=5.0,
        )

        assert initialize.status_code == HTTP_OK
        assert initialize.headers["mcp-session-id"] == session.session_id
    finally:
        stream_socket.close()
        cast("server_runtime._StandaloneHttpServer", server)._httpd.shutdown()  # type: ignore[attr-defined]
        cast("server_runtime._StandaloneHttpServer", server)._httpd.server_close()  # type: ignore[attr-defined]
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
    assert "content_path" in submit_properties


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


def test_planning_session_can_submit_plan_over_mcp_and_handle_planning_consumes_it(
    tmp_path: Path,
) -> None:
    session = AgentSession(
        session_id="planning-session",
        run_id="planning-run",
        drain="planning",
        capabilities=runner_module._default_mcp_capabilities_for_phase("planning"),
    )
    workspace = FsWorkspace(tmp_path)
    registry = server_runtime.build_ralph_tool_registry(session, workspace)
    mcp_server = server_runtime.McpServer(session, workspace, registry)
    payload = {
        "summary": {
            "context": "Ship the planning artifact via Ralph MCP.",
            "scope_items": [
                {"text": "Expose the planning submission tools"},
                {"text": "Persist the plan artifact"},
                {"text": "Validate the plan in the planning phase"},
            ],
        },
        "steps": [{"number": 1, "title": "Submit the plan", "content": "Persist it."}],
        "critical_files": {
            "primary_files": [{"path": "ralph/mcp/tool_artifact.py", "action": "modify"}]
        },
        "risks_mitigations": [
            {"risk": "Tool exposure drift", "mitigation": "Exercise the MCP boundary end-to-end"}
        ],
        "verification_strategy": [
            {"method": "pytest", "expected_outcome": "planning accepts the submitted artifact"}
        ],
    }

    initialize_response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="initialize", msg_id=1),
        server_runtime.ServerState.UNINITIALIZED,
    )
    assert initialize_response is not None

    initialized_response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", msg_id=2),
        state,
    )
    tools_response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=3),
        state,
    )
    submit_response, _ = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            msg_id=4,
            params={
                "name": "ralph_submit_artifact",
                "arguments": {
                    "artifact_type": "plan",
                    "content": json.dumps(payload),
                },
            },
        ),
        state,
    )

    tools_result = cast("dict[str, object]", tools_response.result if tools_response else {})
    tools_list = cast("list[dict[str, object]]", tools_result["tools"])
    tool_names = {cast("str", tool["name"]) for tool in tools_list}

    ctx = PhaseContext.model_construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=object(),
        agents_policy=object(),
        artifacts_policy=object(),
    )
    planning_result = handle_planning(
        InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="planning.txt"),
        ctx,
    )

    assert "ralph_submit_artifact" in tool_names
    assert initialized_response is None
    assert submit_response is not None
    submit_result = cast("dict[str, object]", submit_response.result)
    assert submit_result["isError"] is False
    assert planning_result == [PipelineEvent.AGENT_SUCCESS]
    assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists()

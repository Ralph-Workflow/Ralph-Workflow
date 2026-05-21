"""Integration tests for the standalone Python MCP server runtime."""

from __future__ import annotations

import asyncio
import json
import json as _json
from pathlib import Path
from typing import cast

import pytest
from loguru import logger

# Config imports for multimodal tests
from ralph.mcp.multimodal.capabilities import (
    UNKNOWN_IDENTITY,
    DeliveryMode,
    MultimodalModelIdentity,
    ResolvedCapabilityProfile,
)
from ralph.mcp.protocol import startup
from ralph.mcp.protocol.capability_mapping import McpCapability
from ralph.mcp.protocol.env import MCP_SESSION_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import runtime as server_runtime
from ralph.mcp.server.lifecycle import session_payload_json
from ralph.mcp.server.runtime import FileBackedSession
from ralph.mcp.tools.names import upstream_proxy_tool_name
from ralph.mcp.upstream.client import HttpUpstreamClient, StdioUpstreamClient, make_upstream_client
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
    UpstreamMcpServer,
)
from ralph.mcp.upstream.models import UpstreamCallError
from ralph.mcp.upstream.registry import UpstreamRegistry
from ralph.phases import PhaseContext
from ralph.phases.execution import handle_execution_phase
from ralph.pipeline import runner as runner_module
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PipelineEvent
from ralph.policy.loader import load_policy
from ralph.workspace.fs import FsWorkspace

# Lazy imports for multimodal tests that require optional dependencies
# These are only available when the multimodal feature is fully configured
_lazy_imports: dict[str, object] = {}

HTTP_OK = 200
HTTP_ACCEPTED = 202


@pytest.fixture(autouse=True)
def _isolate_from_upstream_env(monkeypatch: pytest.MonkeyPatch) -> None:
    # Prevent real upstream MCP servers (configured in dev env) from being
    # loaded during tests. Each test provides its own upstream config if needed.
    monkeypatch.delenv(UPSTREAM_MCP_CONFIG_ENV, raising=False)


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
    endpoint: str, method: str, params: dict[str, object] | None = None, *, msg_id: int = 1
) -> dict[str, object]:
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
            str(MCP_SESSION_ENV): json.dumps(
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


def test_session_from_env_preserves_model_identity() -> None:
    session = server_runtime.session_from_env(
        {
            str(MCP_SESSION_ENV): json.dumps(
                {
                    "session_id": "id-session",
                    "run_id": "id-run",
                    "drain": "planning",
                    "capabilities": [],
                    "model_identity": {
                        "provider": "claude",
                        "model_id": "claude-3-5-sonnet",
                        "transport": None,
                    },
                }
            )
        }
    )

    assert session is not None
    assert isinstance(session.model_identity, MultimodalModelIdentity)
    assert session.model_identity.provider == "claude"
    assert session.model_identity.model_id == "claude-3-5-sonnet"


def test_session_from_env_without_model_identity_defaults_to_unknown() -> None:
    session = server_runtime.session_from_env(
        {
            str(MCP_SESSION_ENV): json.dumps(
                {
                    "session_id": "id-session",
                    "run_id": "id-run",
                    "drain": "planning",
                    "capabilities": [],
                }
            )
        }
    )

    assert session is not None
    assert session.model_identity == UNKNOWN_IDENTITY


def test_session_from_env_preserves_capability_profile() -> None:
    session = server_runtime.session_from_env(
        {
            str(MCP_SESSION_ENV): json.dumps(
                {
                    "session_id": "prof-session",
                    "run_id": "prof-run",
                    "drain": "planning",
                    "capabilities": [],
                    "model_identity": {
                        "provider": "claude",
                        "model_id": None,
                        "transport": None,
                    },
                }
            )
        }
    )

    assert session is not None
    profile = session.capability_profile
    assert isinstance(profile, ResolvedCapabilityProfile)
    assert profile.identity.provider == "claude"
    verdict = profile.verdict_for("image")
    assert verdict.delivery == DeliveryMode.INLINE_IMAGE


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
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    session = _session(
        capabilities={
            "WorkspaceRead",
            "ArtifactSubmit",
            "ArtifactPlanWrite",
            "RunReportProgress",
        }
    )

    monkeypatch.setattr(server_runtime, "FastMCP", None)
    monkeypatch.setattr(server_runtime, "Tool", None)
    server = server_runtime.build_fastmcp_server(
        tmp_path, extras=server_runtime.McpServerExtras(session=session)
    )

    assert isinstance(server, server_runtime.FallbackStandaloneServer)

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
    assert {"read_file", "directory_tree", "report_progress", "coordinate"}.issubset(tool_names)


def test_build_standalone_http_server_get_probe_avoids_missing_session_id_error(
    tmp_path: Path,
) -> None:
    session = _session(capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress"})
    workspace = FsWorkspace(tmp_path)
    registry = server_runtime.build_ralph_tool_registry(session, workspace)
    mcp_server = server_runtime.McpServer(session, workspace, registry)

    response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(
            jsonrpc="2.0",
            method="initialize",
            msg_id=1,
        ),
        server_runtime.ServerState.UNINITIALIZED,
    )

    assert response is not None
    assert response.error is None
    assert response.result is not None
    assert state == server_runtime.ServerState.RUNNING
    assert cast("dict[str, object]", response.result)["capabilities"] == {
        "tools": {"listChanged": False},
        "prompts": {"listChanged": False},
        "resources": {"subscribe": False, "listChanged": False},
    }


def test_build_standalone_http_server_initialized_notification_returns_202(
    tmp_path: Path,
) -> None:
    session = _session(capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress"})
    workspace = FsWorkspace(tmp_path)
    registry = server_runtime.build_ralph_tool_registry(session, workspace)
    mcp_server = server_runtime.McpServer(session, workspace, registry)

    response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(
            jsonrpc="2.0",
            method="notifications/initialized",
            msg_id=1,
        ),
        server_runtime.ServerState.UNINITIALIZED,
    )

    assert response is None
    assert state == server_runtime.ServerState.RUNNING


def test_build_standalone_http_server_initialize_sse_omits_null_error_field(
    tmp_path: Path,
) -> None:
    session = _session(capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress"})
    workspace = FsWorkspace(tmp_path)
    registry = server_runtime.build_ralph_tool_registry(session, workspace)
    mcp_server = server_runtime.McpServer(session, workspace, registry)

    response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(
            jsonrpc="2.0",
            method="initialize",
            msg_id=1,
        ),
        server_runtime.ServerState.UNINITIALIZED,
    )

    assert response is not None
    assert response.error is None
    assert response.result is not None
    assert state == server_runtime.ServerState.RUNNING
    assert cast("dict[str, object]", response.result)["serverInfo"] == {
        "name": "ralph-mcp",
        "version": server_runtime.__version__,
    }


def test_build_standalone_http_server_allows_post_while_get_stream_is_open(
    tmp_path: Path,
) -> None:
    session = _session(capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress"})
    workspace = FsWorkspace(tmp_path)
    registry = server_runtime.build_ralph_tool_registry(session, workspace)
    mcp_server = server_runtime.McpServer(session, workspace, registry)

    initialize, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(
            jsonrpc="2.0",
            method="initialize",
            msg_id=1,
        ),
        server_runtime.ServerState.UNINITIALIZED,
    )
    next_response, next_state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(
            jsonrpc="2.0",
            method="notifications/initialized",
            msg_id=2,
        ),
        state,
    )

    assert initialize is not None
    assert initialize.error is None
    assert next_response is None
    assert next_state == server_runtime.ServerState.RUNNING


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
    assert "directory_tree" in tool_names
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
    assert "content_path" not in submit_properties
    assert submit_artifact_schema["required"] == ["artifact_type", "content"]


def test_runtime_main_launches_streamable_http_server(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
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


def test_default_planning_capabilities_do_not_warn_when_policy_is_available(
    tmp_path: Path,
) -> None:
    policy_bundle = load_policy(tmp_path / ".agent")
    warnings: list[str] = []
    sink_id = logger.add(lambda message: warnings.append(str(message)), level="WARNING")
    try:
        observed = runner_module.default_mcp_capabilities_for_phase(
            "planning",
            agents_policy=policy_bundle.agents,
        )
    finally:
        logger.remove(sink_id)

    expected = set(
        runner_module.build_session_mcp_plan(
            transport=None,
            drain="planning",
            workspace_path=None,
            agents_policy=policy_bundle.agents,
        ).capabilities
    )

    assert observed == expected
    assert not any(
        "drain_class_for_session called without agents_policy" in warning for warning in warnings
    )


def test_planning_session_can_submit_plan_over_mcp_and_handle_planning_consumes_it(
    tmp_path: Path,
) -> None:
    policy_bundle = load_policy(tmp_path / ".agent")
    session = AgentSession(
        session_id="planning-session",
        run_id="planning-run",
        drain="planning",
        capabilities=runner_module.default_mcp_capabilities_for_phase(
            "planning",
            agents_policy=policy_bundle.agents,
        ),
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

    initialize, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="initialize", msg_id=1),
        server_runtime.ServerState.UNINITIALIZED,
    )
    initialized_response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", msg_id=2),
        state,
    )
    tools_response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=3),
        state,
    )
    assert tools_response is not None
    tools_result = cast("dict[str, object]", tools_response.result)
    tools_list = cast("list[dict[str, object]]", tools_result["tools"])
    tool_names = {cast("str", tool["name"]) for tool in tools_list}

    submit_response, state = mcp_server.handle_request(
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

    policy = load_policy(tmp_path / ".agent")
    ctx = PhaseContext.model_construct(
        workspace=workspace,
        registry=object(),
        chain_manager=object(),
        pipeline_policy=policy.pipeline,
        agents_policy=object(),
        artifacts_policy=policy.artifacts,
    )
    planning_result = handle_execution_phase(
        InvokeAgentEffect(agent_name="planner", phase="planning", prompt_file="planning.txt"),
        ctx,
    )

    assert initialize is not None
    assert initialize.error is None
    assert initialized_response is None
    assert "ralph_submit_artifact" in tool_names
    assert submit_response is not None
    assert submit_response.error is None
    assert planning_result == [PipelineEvent.AGENT_SUCCESS]
    assert (tmp_path / ".agent" / "artifacts" / "plan.json").exists()


def test_upstream_client_factory_selects_transport_by_server_config() -> None:
    http_server = UpstreamMcpServer(name="fs", transport="http", url="http://localhost:9999")
    stdio_server = UpstreamMcpServer(
        name="gh", transport="stdio", command="npx", args=("mcp-github",)
    )

    http_client = make_upstream_client(http_server)
    stdio_client = make_upstream_client(stdio_server)

    assert isinstance(http_client, HttpUpstreamClient)
    assert isinstance(stdio_client, StdioUpstreamClient)


def test_upstream_proxy_tool_name_follows_canonical_namespace_format() -> None:
    assert (
        upstream_proxy_tool_name("filesystem", "read_file")
        == "ralph_upstream__filesystem__read_file"
    )
    assert (
        upstream_proxy_tool_name("github", "search_repos") == "ralph_upstream__github__search_repos"
    )
    assert upstream_proxy_tool_name("my_server", "my_tool") == "ralph_upstream__my_server__my_tool"


def test_build_fastmcp_server_lists_proxied_upstream_tools(tmp_path: Path) -> None:
    session = AgentSession(
        session_id="session-upstream-list",
        run_id="run-upstream-list",
        drain="development",
        capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress", "UpstreamToolUse"},
    )
    upstream = UpstreamMcpServer(name="myfs", transport="http", url="http://unused")

    def fake_caller(method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "tools/list":
            return {
                "tools": [
                    {
                        "name": "read_remote",
                        "description": "Read a remote file",
                        "inputSchema": {
                            "type": "object",
                            "properties": {"path": {"type": "string"}},
                        },
                    }
                ]
            }
        return {}

    upstream_registry = UpstreamRegistry.build(
        [upstream],
        client_factory=lambda srv: HttpUpstreamClient(srv, caller=fake_caller),
    )

    workspace = FsWorkspace(tmp_path)
    bridge = server_runtime.build_ralph_tool_registry(
        session, workspace, upstream_registry=upstream_registry
    )
    mcp_server = server_runtime.McpServer(session, workspace, bridge)

    _, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="initialize", msg_id=1),
        server_runtime.ServerState.UNINITIALIZED,
    )
    tools_response, _ = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
        state,
    )

    assert tools_response is not None
    tools_result = cast("dict[str, object]", tools_response.result)
    tool_names = {
        cast("str", t["name"]) for t in cast("list[dict[str, object]]", tools_result["tools"])
    }
    assert "read_file" in tool_names
    assert "ralph_upstream__myfs__read_remote" in tool_names


def test_proxied_upstream_tool_call_is_forwarded_after_policy_check(tmp_path: Path) -> None:
    session = AgentSession(
        session_id="session-proxy-call",
        run_id="run-proxy-call",
        drain="development",
        capabilities={"WorkspaceRead", "ArtifactSubmit", "UpstreamToolUse"},
    )
    calls_received: list[dict[str, object]] = []
    upstream = UpstreamMcpServer(name="remote", transport="http", url="http://unused")

    def fake_caller(method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "tools/list":
            return {"tools": [{"name": "ping", "description": "Ping tool", "inputSchema": {}}]}
        if method == "tools/call":
            calls_received.append(dict(params))
            return {"content": [{"type": "text", "text": "pong"}]}
        return {}

    upstream_registry = UpstreamRegistry.build(
        [upstream],
        client_factory=lambda srv: HttpUpstreamClient(srv, caller=fake_caller),
    )

    workspace = FsWorkspace(tmp_path)
    bridge = server_runtime.build_ralph_tool_registry(
        session, workspace, upstream_registry=upstream_registry
    )
    mcp_server = server_runtime.McpServer(session, workspace, bridge)

    _, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="initialize", msg_id=1),
        server_runtime.ServerState.UNINITIALIZED,
    )
    _, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", msg_id=2),
        state,
    )
    call_response, _ = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            msg_id=3,
            params={"name": "ralph_upstream__remote__ping", "arguments": {}},
        ),
        state,
    )

    assert call_response is not None
    assert call_response.error is None
    assert len(calls_received) == 1


def test_upstream_registry_catalog_excludes_unhealthy_upstream_servers() -> None:
    good = UpstreamMcpServer(name="healthy", transport="http", url="http://unused")
    bad = UpstreamMcpServer(name="broken", transport="http", url="http://unused")

    def good_caller(method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "tools/list":
            return {"tools": [{"name": "ping", "description": "Ping", "inputSchema": {}}]}
        return {}

    def bad_caller(method: str, params: dict[str, object]) -> dict[str, object]:
        raise UpstreamCallError("server unreachable")

    def client_factory(server: UpstreamMcpServer) -> HttpUpstreamClient:
        if server.name == "healthy":
            return HttpUpstreamClient(server, caller=good_caller)
        return HttpUpstreamClient(server, caller=bad_caller)

    registry = UpstreamRegistry.build(
        [good, bad],
        client_factory=client_factory,
        on_unreachable="warn_and_skip",
    )
    definitions = registry.tool_definitions()

    assert len(definitions) == 1
    assert definitions[0].alias == "ralph_upstream__healthy__ping"
    assert not any("broken" in d.alias for d in definitions)


def test_upstream_policy_blocks_proxied_tools_without_upstream_capability(
    tmp_path: Path,
) -> None:
    assert McpCapability.UPSTREAM_TOOL_USE == "UpstreamToolUse"

    session = AgentSession(
        session_id="session-policy-upstream-deny",
        run_id="run-policy-upstream-deny",
        drain="development",
        capabilities={"WorkspaceRead", "ArtifactSubmit"},
    )
    upstream = UpstreamMcpServer(name="srv", transport="http", url="http://unused")

    def fake_caller(method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "tools/list":
            return {
                "tools": [{"name": "do_thing", "description": "Does a thing", "inputSchema": {}}]
            }
        return {}

    upstream_registry = UpstreamRegistry.build(
        [upstream],
        client_factory=lambda srv: HttpUpstreamClient(srv, caller=fake_caller),
    )
    workspace = FsWorkspace(tmp_path)
    bridge = server_runtime.build_ralph_tool_registry(
        session, workspace, upstream_registry=upstream_registry
    )
    mcp_server = server_runtime.McpServer(session, workspace, bridge)

    _, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="initialize", msg_id=1),
        server_runtime.ServerState.UNINITIALIZED,
    )
    tools_response, _ = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
        state,
    )

    assert tools_response is not None
    tools_result = cast("dict[str, object]", tools_response.result)
    tool_names = {
        cast("str", t["name"]) for t in cast("list[dict[str, object]]", tools_result["tools"])
    }
    assert "ralph_upstream__srv__do_thing" not in tool_names


def test_upstream_policy_allows_proxied_tools_with_upstream_capability(
    tmp_path: Path,
) -> None:
    assert McpCapability.UPSTREAM_TOOL_USE == "UpstreamToolUse"

    session = AgentSession(
        session_id="session-policy-upstream-allow",
        run_id="run-policy-upstream-allow",
        drain="development",
        capabilities={"WorkspaceRead", "ArtifactSubmit", McpCapability.UPSTREAM_TOOL_USE},
    )
    upstream = UpstreamMcpServer(name="srv2", transport="http", url="http://unused")

    def fake_caller(method: str, params: dict[str, object]) -> dict[str, object]:
        if method == "tools/list":
            return {
                "tools": [{"name": "do_thing", "description": "Does a thing", "inputSchema": {}}]
            }
        return {}

    upstream_registry = UpstreamRegistry.build(
        [upstream],
        client_factory=lambda srv: HttpUpstreamClient(srv, caller=fake_caller),
    )
    workspace = FsWorkspace(tmp_path)
    bridge = server_runtime.build_ralph_tool_registry(
        session, workspace, upstream_registry=upstream_registry
    )
    mcp_server = server_runtime.McpServer(session, workspace, bridge)

    _, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="initialize", msg_id=1),
        server_runtime.ServerState.UNINITIALIZED,
    )
    tools_response, _ = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
        state,
    )

    assert tools_response is not None
    tools_result = cast("dict[str, object]", tools_response.result)
    tool_names = {
        cast("str", t["name"]) for t in cast("list[dict[str, object]]", tools_result["tools"])
    }
    assert "ralph_upstream__srv2__do_thing" in tool_names


# =============================================================================
# Image content serialization tests (Task 3)
# =============================================================================


class TestFileBackedSessionModelIdentity:
    """Tests for FileBackedSession.model_identity property."""

    def test_file_backed_session_restores_known_model_identity(self, tmp_path: Path) -> None:
        """FileBackedSession.model_identity returns the deserialized MultimodalModelIdentity."""

        payload = {
            "session_id": "sid-fbs",
            "run_id": "run-fbs",
            "drain": "development",
            "capabilities": ["WorkspaceRead"],
            "model_identity": {
                "provider": "anthropic",
                "model_id": "claude-3-5-sonnet",
                "transport": "cli",
            },
        }
        session_file = tmp_path / "session.json"
        session_file.write_text(json.dumps(payload), encoding="utf-8")

        session = FileBackedSession(session_file)
        identity = session.model_identity
        assert isinstance(identity, MultimodalModelIdentity)
        assert identity.provider == "anthropic"
        assert identity.model_id == "claude-3-5-sonnet"
        assert identity.transport == "cli"

    def test_file_backed_session_falls_back_to_unknown_identity_when_absent(
        self, tmp_path: Path
    ) -> None:
        """FileBackedSession.model_identity returns UNKNOWN_IDENTITY when payload omits it."""

        payload = {
            "session_id": "sid-no-mi",
            "run_id": "run-no-mi",
            "drain": "development",
            "capabilities": ["WorkspaceRead"],
        }
        session_file = tmp_path / "session.json"
        session_file.write_text(json.dumps(payload), encoding="utf-8")

        session = FileBackedSession(session_file)
        identity = session.model_identity
        assert identity == UNKNOWN_IDENTITY

    def test_lifecycle_payload_roundtrip_preserves_model_identity(self, tmp_path: Path) -> None:
        """session_payload_json + FileBackedSession restores the same model identity."""

        agent_session = AgentSession(
            session_id="sid-rt",
            run_id="run-rt",
            drain="development",
            capabilities={"WorkspaceRead"},
            model_identity=MultimodalModelIdentity(
                provider="anthropic", model_id="claude-opus-4-7", transport="api"
            ),
        )
        payload_str = session_payload_json(agent_session)
        session_file = tmp_path / "session-rt.json"
        session_file.write_text(payload_str, encoding="utf-8")

        restored = FileBackedSession(session_file)
        identity = restored.model_identity
        assert isinstance(identity, MultimodalModelIdentity)
        assert identity.provider == "anthropic"
        assert identity.model_id == "claude-opus-4-7"
        assert identity.transport == "api"
        # Verify the raw payload also contains model_identity
        raw = _json.loads(payload_str)
        assert raw["model_identity"]["provider"] == "anthropic"

"""Integration tests for the standalone Python MCP server runtime."""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from loguru import logger

# Config imports for multimodal tests
from ralph.config.mcp_models import McpConfig, MediaConfig
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
from tests._support.typed_accessors import (
    must_dict_list,
    must_mapping,
    must_str,
)
from tests.mcp.test_md_plan_spec import _plan_document

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


def test_build_standalone_http_server_falls_back_without_mcp_dependency(
    tmp_path: Path,
) -> None:
    # Property A: there is no alternate FastMCP path. The single production
    # _FallbackStandaloneServer (via build_standalone_http_server) is the
    # only server construction surface. This test pins the shipped path.
    session = _session(
        capabilities={
            "WorkspaceRead",
            "ArtifactSubmit",
            "ArtifactPlanWrite",
            "RunReportProgress",
        }
    )
    server = server_runtime.build_standalone_http_server(
        tmp_path, extras=server_runtime.McpServerExtras(session=session)
    )

    mcp_server = server._mcp_server
    state = server_runtime.ServerState.UNINITIALIZED

    initialize_response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="initialize", msg_id=1),
        state,
    )
    assert initialize_response is not None
    initialize_result = must_mapping(initialize_response.result)
    assert must_mapping(initialize_result["serverInfo"])["name"] == "ralph-mcp"
    assert must_mapping(initialize_result["serverInfo"])["version"]
    assert must_mapping(initialize_result["capabilities"])["prompts"] == {"listChanged": False}
    assert must_mapping(initialize_result["capabilities"])["resources"] == {
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
    tools_result = must_mapping(tools_response.result)
    tool_names = {tool["name"] for tool in must_dict_list(tools_result["tools"])}
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
    assert must_mapping(response.result)["capabilities"] == {
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
    assert must_mapping(response.result)["serverInfo"] == {
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


def test_build_standalone_http_server_filters_tools_by_session_capabilities(tmp_path: Path) -> None:
    session = AgentSession(
        session_id="session-filtered",
        run_id="run-filtered",
        drain="planning",
        capabilities={"WorkspaceRead", "ArtifactSubmit"},
    )

    workspace = FsWorkspace(tmp_path)
    registry = server_runtime.build_ralph_tool_registry(session, workspace)
    mcp_server = server_runtime.McpServer(session, workspace, registry)
    _, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="initialize", msg_id=1),
        server_runtime.ServerState.UNINITIALIZED,
    )
    tools_response, _ = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
        state,
    )
    assert tools_response is not None
    tools_result = must_mapping(tools_response.result)
    tool_names = {must_str(t["name"]) for t in must_dict_list(tools_result["tools"])}

    assert "read_file" in tool_names
    assert "directory_tree" in tool_names
    assert "ralph_submit_md_artifact" in tool_names
    assert "exec" not in tool_names
    assert "write_file" not in tool_names


def test_build_standalone_http_server_preserves_registry_input_schema(tmp_path: Path) -> None:
    workspace = FsWorkspace(tmp_path)
    session = AgentSession(
        session_id="schema-session",
        run_id="schema-run",
        drain="standalone",
        capabilities=server_runtime._all_capability_values(),
    )
    registry = server_runtime.build_ralph_tool_registry(session, workspace)
    mcp_server = server_runtime.McpServer(session, workspace, registry)
    _, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="initialize", msg_id=1),
        server_runtime.ServerState.UNINITIALIZED,
    )
    tools_response, _ = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
        state,
    )
    assert tools_response is not None
    tools_result = must_mapping(tools_response.result)
    tools_list = must_dict_list(tools_result["tools"])
    tools = {must_str(t["name"]): t for t in tools_list}

    read_env_schema = must_mapping(tools["read_env"]["inputSchema"])
    properties = must_mapping(read_env_schema["properties"])
    assert read_env_schema["required"] == ["name"]
    assert "name" in properties

    submit_artifact_schema_raw = tools["ralph_submit_md_artifact"]["inputSchema"]
    submit_artifact_schema = must_mapping(submit_artifact_schema_raw)
    submit_properties = must_mapping(submit_artifact_schema["properties"])
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


def test_build_standalone_http_server_normalizes_tool_result_payload(tmp_path: Path) -> None:
    session = AgentSession(
        session_id="normalize-session",
        run_id="normalize-run",
        drain="standalone",
        capabilities=server_runtime._all_capability_values(),
    )
    workspace = FsWorkspace(tmp_path)
    registry = server_runtime.build_ralph_tool_registry(session, workspace)
    mcp_server = server_runtime.McpServer(session, workspace, registry)
    _, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(jsonrpc="2.0", method="initialize", msg_id=1),
        server_runtime.ServerState.UNINITIALIZED,
    )
    response, _ = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            msg_id=2,
            params={"name": "report_progress", "arguments": {"status": "running"}},
        ),
        state,
    )

    assert response is not None
    assert response.error is None
    result = must_mapping(response.result or {})
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
        "skills_mcp": {
            "skills": [
                "test-driven-development",
                "verification-before-completion",
            ],
            "mcps": [],
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
    tools_result = must_mapping(tools_response.result)
    tools_list = must_dict_list(tools_result["tools"])
    tool_names = {must_str(tool["name"]) for tool in tools_list}

    submit_response, state = mcp_server.handle_request(
        server_runtime.JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            msg_id=4,
            params={
                "name": "ralph_submit_md_artifact",
                "arguments": {
                    "artifact_type": "plan",
                    "content": _plan_document().replace(
                        "Concurrent refresh requests can race.",
                        must_str(must_mapping(payload["summary"])["context"]),
                    ),
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
    assert "ralph_submit_md_artifact" in tool_names
    assert submit_response is not None
    assert submit_response.error is None
    assert planning_result == [PipelineEvent.AGENT_SUCCESS]
    assert (tmp_path / ".agent" / "artifacts" / "plan.md").exists()


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


def test_build_standalone_http_server_lists_proxied_upstream_tools(tmp_path: Path) -> None:
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
    tools_result = must_mapping(tools_response.result)
    tool_names = {must_str(t["name"]) for t in must_dict_list(tools_result["tools"])}
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
    tools_result = must_mapping(tools_response.result)
    tool_names = {must_str(t["name"]) for t in must_dict_list(tools_result["tools"])}
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
    tools_result = must_mapping(tools_response.result)
    tool_names = {must_str(t["name"]) for t in must_dict_list(tools_result["tools"])}
    assert "ralph_upstream__srv2__do_thing" in tool_names

# === consolidated from test_mcp_server_multimodal_tool_visibility_2.py ===

class TestMultimodalToolVisibility:
    """Tests for multimodal tool client capability filtering (Task 5)."""

    def test_text_only_client_does_not_see_read_image_when_media_disabled(
        self, tmp_path: Path
    ) -> None:
        """When media.enabled=False, read_image is absent from tools/list for text-only client."""
        session = AgentSession(
            session_id="session-text-only",
            run_id="run-text-only",
            drain="development",
            capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress"},
        )
        workspace = FsWorkspace(tmp_path)
        config = McpConfig(media=MediaConfig(enabled=False))
        bridge = server_runtime.build_ralph_tool_registry(session, workspace, mcp_config=config)
        mcp_server = server_runtime.McpServer(session, workspace, bridge)

        # Initialize with NO multimodal capability
        _, state = mcp_server.handle_request(
            server_runtime.JsonRpcRequest(
                jsonrpc="2.0",
                method="initialize",
                msg_id=1,
                params={"capabilities": {}},
            ),
            server_runtime.ServerState.UNINITIALIZED,
        )
        tools_response, _ = mcp_server.handle_request(
            server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
            state,
        )

        assert tools_response is not None
        tools_result = must_mapping(tools_response.result)
        tool_names = {must_str(t["name"]) for t in must_dict_list(tools_result["tools"])}
        assert "read_image" not in tool_names

    def test_text_only_client_does_not_see_read_image_when_media_enabled(
        self, tmp_path: Path
    ) -> None:
        """When media.enabled=True but client has no multimodal capability, read_image is hidden."""
        session = AgentSession(
            session_id="session-text-only-media",
            run_id="run-text-only-media",
            drain="development",
            capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress", "media.read"},
        )
        workspace = FsWorkspace(tmp_path)
        config = McpConfig(media=MediaConfig(enabled=True))
        bridge = server_runtime.build_ralph_tool_registry(session, workspace, mcp_config=config)
        mcp_server = server_runtime.McpServer(session, workspace, bridge)

        # Initialize with NO multimodal capability in client declaration
        _, state = mcp_server.handle_request(
            server_runtime.JsonRpcRequest(
                jsonrpc="2.0",
                method="initialize",
                msg_id=1,
                params={"capabilities": {}},
            ),
            server_runtime.ServerState.UNINITIALIZED,
        )
        tools_response, _ = mcp_server.handle_request(
            server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
            state,
        )

        assert tools_response is not None
        tools_result = must_mapping(tools_response.result)
        tool_names = {must_str(t["name"]) for t in must_dict_list(tools_result["tools"])}
        # Text-only client should NOT see read_image even when media is enabled on server
        assert "read_image" not in tool_names

    def test_multimodal_client_sees_read_image_when_media_enabled(self, tmp_path: Path) -> None:
        """When media.enabled=True and client declares multimodal support, read_image IS visible."""
        session = AgentSession(
            session_id="session-multimodal",
            run_id="run-multimodal",
            drain="development",
            capabilities={
                "WorkspaceRead",
                "ArtifactSubmit",
                "RunReportProgress",
                "media.read",
            },
        )
        workspace = FsWorkspace(tmp_path)
        config = McpConfig(media=MediaConfig(enabled=True))
        bridge = server_runtime.build_ralph_tool_registry(session, workspace, mcp_config=config)
        mcp_server = server_runtime.McpServer(session, workspace, bridge)

        # Initialize WITH multimodal capability declaration
        _, state = mcp_server.handle_request(
            server_runtime.JsonRpcRequest(
                jsonrpc="2.0",
                method="initialize",
                msg_id=1,
                params={"capabilities": {"image": {}, "media": {}}},
            ),
            server_runtime.ServerState.UNINITIALIZED,
        )
        tools_response, _ = mcp_server.handle_request(
            server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
            state,
        )

        assert tools_response is not None
        tools_result = must_mapping(tools_response.result)
        tool_names = {must_str(t["name"]) for t in must_dict_list(tools_result["tools"])}
        # Multimodal-capable client SHOULD see read_image
        assert "read_image" in tool_names

    def test_baseline_text_only_tools_unchanged_when_media_enabled(self, tmp_path: Path) -> None:
        """Text-only tools are identical regardless of media.enabled setting."""
        capabilities = {
            "WorkspaceRead",
            "ArtifactSubmit",
            "ArtifactPlanWrite",
            "RunReportProgress",
        }

        # Without media
        session1 = AgentSession(
            session_id="session-baseline",
            run_id="run-baseline",
            drain="development",
            capabilities=capabilities,
        )
        workspace1 = FsWorkspace(tmp_path)
        config1 = McpConfig(media=MediaConfig(enabled=False))
        bridge1 = server_runtime.build_ralph_tool_registry(session1, workspace1, mcp_config=config1)
        mcp_server1 = server_runtime.McpServer(session1, workspace1, bridge1)

        # With media
        session2 = AgentSession(
            session_id="session-baseline2",
            run_id="run-baseline2",
            drain="development",
            capabilities=capabilities,
        )
        workspace2 = FsWorkspace(tmp_path)
        config2 = McpConfig(media=MediaConfig(enabled=True))
        bridge2 = server_runtime.build_ralph_tool_registry(session2, workspace2, mcp_config=config2)
        mcp_server2 = server_runtime.McpServer(session2, workspace2, bridge2)

        # Initialize both with text-only client capabilities
        _, state1 = mcp_server1.handle_request(
            server_runtime.JsonRpcRequest(
                jsonrpc="2.0",
                method="initialize",
                msg_id=1,
                params={"capabilities": {}},
            ),
            server_runtime.ServerState.UNINITIALIZED,
        )
        _, state2 = mcp_server2.handle_request(
            server_runtime.JsonRpcRequest(
                jsonrpc="2.0",
                method="initialize",
                msg_id=1,
                params={"capabilities": {}},
            ),
            server_runtime.ServerState.UNINITIALIZED,
        )

        tools_response1, _ = mcp_server1.handle_request(
            server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
            state1,
        )
        tools_response2, _ = mcp_server2.handle_request(
            server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
            state2,
        )

        # Both should have the same text-only tools (read_image absent from both)
        assert tools_response1 is not None
        assert tools_response2 is not None

        result1 = must_mapping(tools_response1.result)
        result2 = must_mapping(tools_response2.result)

        tools1 = must_dict_list(result1["tools"])
        tools2 = must_dict_list(result2["tools"])

        names1 = {must_str(t["name"]) for t in tools1}
        names2 = {must_str(t["name"]) for t in tools2}

        # read_file should be in both
        assert "read_file" in names1
        assert "read_file" in names2
        # read_image should NOT be in either (both text-only clients)
        assert "read_image" not in names1
        assert "read_image" not in names2

    def test_multimodal_client_sees_read_image_by_default(self, tmp_path: Path) -> None:
        """When using default McpConfig (media enabled by default) and client declares
        multimodal support, read_image IS visible without any [media] config section."""
        session = AgentSession(
            session_id="session-default-multimodal",
            run_id="run-default-multimodal",
            drain="development",
            capabilities={
                "WorkspaceRead",
                "ArtifactSubmit",
                "RunReportProgress",
                "media.read",
            },
        )
        workspace = FsWorkspace(tmp_path)
        config = McpConfig()  # Default: media.enabled = True
        bridge = server_runtime.build_ralph_tool_registry(session, workspace, mcp_config=config)
        mcp_server = server_runtime.McpServer(session, workspace, bridge)

        # Initialize WITH multimodal capability declaration
        _, state = mcp_server.handle_request(
            server_runtime.JsonRpcRequest(
                jsonrpc="2.0",
                method="initialize",
                msg_id=1,
                params={"capabilities": {"image": {}, "media": {}}},
            ),
            server_runtime.ServerState.UNINITIALIZED,
        )
        tools_response, _ = mcp_server.handle_request(
            server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
            state,
        )

        assert tools_response is not None
        tools_result = must_mapping(tools_response.result)
        tool_names = {must_str(t["name"]) for t in must_dict_list(tools_result["tools"])}
        # Multimodal-capable client SHOULD see read_image with default config
        assert "read_image" in tool_names

    def test_text_only_client_does_not_see_read_image_by_default(self, tmp_path: Path) -> None:
        """When using default McpConfig (media enabled by default) but client has no
        multimodal capability, read_image remains hidden."""
        session = AgentSession(
            session_id="session-default-textonly",
            run_id="run-default-textonly",
            drain="development",
            capabilities={"WorkspaceRead", "ArtifactSubmit", "RunReportProgress", "media.read"},
        )
        workspace = FsWorkspace(tmp_path)
        config = McpConfig()  # Default: media.enabled = True
        bridge = server_runtime.build_ralph_tool_registry(session, workspace, mcp_config=config)
        mcp_server = server_runtime.McpServer(session, workspace, bridge)

        # Initialize with NO multimodal capability in client declaration
        _, state = mcp_server.handle_request(
            server_runtime.JsonRpcRequest(
                jsonrpc="2.0",
                method="initialize",
                msg_id=1,
                params={"capabilities": {}},
            ),
            server_runtime.ServerState.UNINITIALIZED,
        )
        tools_response, _ = mcp_server.handle_request(
            server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
            state,
        )

        assert tools_response is not None
        tools_result = must_mapping(tools_response.result)
        tool_names = {must_str(t["name"]) for t in must_dict_list(tools_result["tools"])}
        # Text-only client should NOT see read_image even with default media config
        assert "read_image" not in tool_names

    def test_multimodal_client_does_not_see_read_image_when_explicitly_disabled(
        self, tmp_path: Path
    ) -> None:
        """When media.enabled=false explicitly, read_image is absent even for multimodal client."""
        session = AgentSession(
            session_id="session-explicit-off",
            run_id="run-explicit-off",
            drain="development",
            capabilities={
                "WorkspaceRead",
                "ArtifactSubmit",
                "RunReportProgress",
                "media.read",
            },
        )
        workspace = FsWorkspace(tmp_path)
        config = McpConfig(media=MediaConfig(enabled=False))
        bridge = server_runtime.build_ralph_tool_registry(session, workspace, mcp_config=config)
        mcp_server = server_runtime.McpServer(session, workspace, bridge)

        # Initialize WITH multimodal capability declaration
        _, state = mcp_server.handle_request(
            server_runtime.JsonRpcRequest(
                jsonrpc="2.0",
                method="initialize",
                msg_id=1,
                params={"capabilities": {"image": {}, "media": {}}},
            ),
            server_runtime.ServerState.UNINITIALIZED,
        )
        tools_response, _ = mcp_server.handle_request(
            server_runtime.JsonRpcRequest(jsonrpc="2.0", method="tools/list", msg_id=2),
            state,
        )

        assert tools_response is not None
        tools_result = must_mapping(tools_response.result)
        tool_names = {must_str(t["name"]) for t in must_dict_list(tools_result["tools"])}
        # Multimodal-capable client should NOT see read_image when media is explicitly disabled
        assert "read_image" not in tool_names

"""Integration tests for the standalone Python MCP server runtime."""

# Property A: there is no alternate FastMCP path. The single production
# _FallbackStandaloneServer (via build_standalone_http_server) is the
# only server construction surface. This test pins the shipped path.

from __future__ import annotations

from pathlib import Path

import pytest

# Config imports for multimodal tests
from ralph.mcp.protocol import startup
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import runtime as server_runtime
from ralph.mcp.tools.coordination import ImageContent, ToolContent, ToolResult
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
)
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
    document = """---
type: plan
noop: true
---
"""

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
                    "content": document,
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
    submit_result = must_mapping(submit_response.result)
    assert submit_result["isError"] is False
    assert planning_result == [PipelineEvent.AGENT_SUCCESS]
    assert (tmp_path / ".agent" / "artifacts" / "plan.md").exists()


# =============================================================================
# Image content serialization tests (Task 3)
# =============================================================================


class TestImageContentSerialization:
    """Tests for image content block serialization (Task 3)."""

    def test_legacy_text_content_to_dict_format(self) -> None:
        """Legacy ToolContent.text_content().to_dict() yields {'type':'text','text':...}."""
        text_block = ToolContent.text_content("hello world")
        result = text_block.to_dict()

        assert result == {"type": "text", "text": "hello world"}
        assert "type" in result
        assert result["type"] == "text"
        assert "text" in result
        assert result["text"] == "hello world"

    def test_image_content_to_dict_format(self) -> None:
        """ImageContent serializes to {'type':'image','data':<base64>,'mimeType':<str>}."""
        image_block = ImageContent(data="SGVsbG8gV29ybGQ=", mime_type="image/png")
        result = image_block.to_dict()

        assert result["type"] == "image"
        assert result["data"] == "SGVsbG8gV29ybGQ="
        assert result["mimeType"] == "image/png"
        assert "type" in result
        assert "data" in result
        assert "mimeType" in result

    def test_image_content_type_is_explicit_image(self) -> None:
        """ImageContent.type field is always 'image', not derived from mime_type."""
        block = ImageContent(data="abc123", mime_type="image/jpeg")
        assert block.type == "image"
        assert block.to_dict()["type"] == "image"

    def test_tool_result_with_text_and_image_content(self) -> None:
        """ToolResult.to_dict() with [text, image] preserves order and correct shapes."""
        result = ToolResult(
            content=[
                ToolContent.text_content("header"),
                ImageContent(data="SGVsbG8gV29ybGQ=", mime_type="image/png"),
                ToolContent.text_content("footer"),
            ],
            is_error=False,
        )
        serialized = result.to_dict()

        content_list = must_dict_list(serialized["content"])
        expected_block_count = 3
        assert len(content_list) == expected_block_count
        assert content_list[0] == {"type": "text", "text": "header"}
        assert content_list[1] == {
            "type": "image",
            "data": "SGVsbG8gV29ybGQ=",
            "mimeType": "image/png",
        }
        assert content_list[2] == {"type": "text", "text": "footer"}

    def test_tool_result_serialize_content_blocks_no_stringify_fallback(self) -> None:
        """Runtime serialization does not silently stringify image blocks."""
        result = ToolResult(
            content=[
                ToolContent.text_content("hello"),
                ImageContent(data="SGVsbG8gV29ybGQ=", mime_type="image/png"),
            ],
            is_error=False,
        )
        serialized = result.to_dict()
        content_list = must_dict_list(serialized["content"])

        # First block should be text with correct structure
        assert content_list[0] == {"type": "text", "text": "hello"}
        # Second block should be image with correct structure, NOT stringified
        expected_image_block = {
            "type": "image",
            "data": "SGVsbG8gV29ybGQ=",
            "mimeType": "image/png",
        }
        assert content_list[1] == expected_image_block
        # Verify keys are correct - no stray 'text' key in image block
        assert "text" not in content_list[1]

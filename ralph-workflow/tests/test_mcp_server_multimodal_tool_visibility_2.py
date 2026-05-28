"""Integration tests for the standalone Python MCP server runtime."""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

import pytest

# Config imports for multimodal tests
from ralph.config.mcp_models import McpConfig, MediaConfig
from ralph.mcp.protocol import startup
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import runtime as server_runtime
from ralph.mcp.upstream.config import (
    UPSTREAM_MCP_CONFIG_ENV,
)
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

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
        tools_result = cast("dict[str, object]", tools_response.result)
        tool_names = {
            cast("str", t["name"]) for t in cast("list[dict[str, object]]", tools_result["tools"])
        }
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
        tools_result = cast("dict[str, object]", tools_response.result)
        tool_names = {
            cast("str", t["name"]) for t in cast("list[dict[str, object]]", tools_result["tools"])
        }
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
        tools_result = cast("dict[str, object]", tools_response.result)
        tool_names = {
            cast("str", t["name"]) for t in cast("list[dict[str, object]]", tools_result["tools"])
        }
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

        result1 = cast("dict[str, object]", tools_response1.result)
        result2 = cast("dict[str, object]", tools_response2.result)

        tools1 = cast("list[dict[str, object]]", result1["tools"])
        tools2 = cast("list[dict[str, object]]", result2["tools"])

        names1 = {cast("str", t["name"]) for t in tools1}
        names2 = {cast("str", t["name"]) for t in tools2}

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
        tools_result = cast("dict[str, object]", tools_response.result)
        tool_names = {
            cast("str", t["name"]) for t in cast("list[dict[str, object]]", tools_result["tools"])
        }
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
        tools_result = cast("dict[str, object]", tools_response.result)
        tool_names = {
            cast("str", t["name"]) for t in cast("list[dict[str, object]]", tools_result["tools"])
        }
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
        tools_result = cast("dict[str, object]", tools_response.result)
        tool_names = {
            cast("str", t["name"]) for t in cast("list[dict[str, object]]", tools_result["tools"])
        }
        # Multimodal-capable client should NOT see read_image when media is explicitly disabled
        assert "read_image" not in tool_names

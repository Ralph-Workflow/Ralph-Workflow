"""Integration tests for MCP wire-level roundtrip.

- HTTP transport tests: replaced with in-process McpServer.handle_request() calls —
  no daemon threads, no socket polling, no httpx against localhost.
- stdio transport: uses the fake stdio server fixture to test Ralph's MCP client.
"""

from __future__ import annotations

import base64
import tempfile
from pathlib import Path
from typing import Any, cast

import pytest

from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.runtime import (
    JsonRpcRequest,
    McpServer,
    ServerState,
    build_ralph_tool_registry,
)
from ralph.mcp.tools.names import RalphToolName
from ralph.workspace.fs import FsWorkspace

pytestmark = pytest.mark.subprocess_e2e

# Description length bounds enforced by the quality bar
_MIN_DESCRIPTION_CHARS = 20
_MAX_DESCRIPTION_CHARS = 500

# Content used by the append_file roundtrip assertion
_APPEND_CONTENT = "hi"

# Tiny 1x1 PNG image for inline image delivery tests
_TINY_PNG_BYTES = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mNk"
    "+M9QDwADhgGAWjR9awAAAABJRU5ErkJggg=="
)

# Minimal PDF bytes for resource-reference delivery tests
_TINY_PDF_BYTES = b"%PDF-1.4 tiny test pdf\n%%EOF"

# Capabilities required by the built-in Ralph tools
_REQUIRED_CAPABILITIES = {
    "WorkspaceRead",
    "WorkspaceWriteAny",
    "WorkspaceMetadataRead",
    "WorkspaceEdit",
    "WorkspaceDelete",
    "GitStatusRead",
    "ProcessExecBounded",
    "ArtifactSubmit",
    "RunReportProgress",
    "EnvRead",
    "WebSearch",
    "WebVisit",
}


def _build_server(
    workspace_path: Path,
    *,
    session_id: str = "test-session",
    drain: str = "test",
) -> McpServer:
    workspace = FsWorkspace(workspace_path)
    session = AgentSession(
        session_id=session_id,
        run_id="test-run",
        drain=drain,
        capabilities=_REQUIRED_CAPABILITIES,
    )
    registry = build_ralph_tool_registry(session, workspace)
    return McpServer(session, workspace, registry)


def _do_initialize(server: McpServer) -> ServerState:
    """Send initialize + notifications/initialized; return running ServerState."""
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="initialize",
        params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0.00"},
        },
        msg_id=1,
    )
    resp, state = server.handle_request(req, ServerState.UNINITIALIZED)
    assert resp is not None, "initialize returned None"
    init_result = cast("dict[str, Any]", resp.result)
    assert init_result["protocolVersion"] == "2024-11-05"
    notif = JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", params={})
    none_resp, state = server.handle_request(notif, state)
    assert none_resp is None
    return state


def _do_tools_list(server: McpServer, state: ServerState) -> list[dict[str, object]]:
    req = JsonRpcRequest(jsonrpc="2.0", method="tools/list", params={}, msg_id=2)
    resp, _ = server.handle_request(req, state)
    assert resp is not None and resp.result is not None, f"tools/list failed: {resp}"
    return cast("list[dict[str, Any]]", cast("dict[str, Any]", resp.result)["tools"])


def _do_tool_call(
    server: McpServer,
    state: ServerState,
    call_id: list[int],
    name: str,
    args: dict[str, object],
) -> dict[str, object]:
    """Make a tools/call request and return the MCP result object."""
    call_id[0] += 1
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={"name": name, "arguments": args},
        msg_id=call_id[0],
    )
    resp, _ = server.handle_request(req, state)
    assert resp is not None, f"tools/call {name!r} returned None"
    return cast("dict[str, Any]", resp.result)


def _build_multimodal_server(
    workspace_path: Path,
    *,
    provider: str = "unknown",
    session_id: str = "test-session",
    drain: str = "development",
) -> McpServer:
    workspace = FsWorkspace(workspace_path)
    session = AgentSession(
        session_id=session_id,
        run_id="test-run",
        drain=drain,
        capabilities=_REQUIRED_CAPABILITIES,
        model_identity=MultimodalModelIdentity(provider=provider),
    )
    registry = build_ralph_tool_registry(session, workspace)
    return McpServer(session, workspace, registry)


def _initialize_multimodal(server: McpServer) -> ServerState:
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="initialize",
        params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0.0"},
        },
        msg_id=1,
    )
    resp, state = server.handle_request(req, ServerState.UNINITIALIZED)
    assert resp is not None, "initialize returned None"
    notif = JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", params={})
    _, state = server.handle_request(notif, state)
    return state


@pytest.mark.integration
class TestMultimodalToolRoundtrip:
    """Black-box multimodal tool/resource roundtrips via McpServer.handle_request()."""

    def test_read_media_png_with_claude_session_returns_inline_image(self, tmp_path: Path) -> None:
        """PNG file with Claude model identity delivers an inline image block."""
        png_file = tmp_path / "screenshot.png"
        png_file.write_bytes(_TINY_PNG_BYTES)

        server = _build_multimodal_server(tmp_path, provider="claude")
        state = _initialize_multimodal(server)

        call_id = [10]
        result = _do_tool_call(
            server, state, call_id, str(RalphToolName.READ_MEDIA), {"path": "screenshot.png"}
        )

        assert result.get("isError") is not True, f"read_media returned error: {result}"
        content = cast("list[dict[str, Any]]", result.get("content", []))
        assert len(content) == 1
        block = content[0]
        assert block.get("type") == "image", (
            f"Expected inline image block, got type={block.get('type')!r}"
        )
        assert block.get("mimeType") == "image/png"
        assert isinstance(block.get("data"), str) and len(block["data"]) > 0

    def test_read_media_pdf_returns_resource_reference_and_is_retrievable(
        self, tmp_path: Path
    ) -> None:
        """PDF with unknown provider delivers resource_reference retrievable via resources/read."""
        pdf_file = tmp_path / "report.pdf"
        pdf_file.write_bytes(_TINY_PDF_BYTES)

        server = _build_multimodal_server(tmp_path, provider="unknown-provider")
        state = _initialize_multimodal(server)

        call_id = [10]
        result = _do_tool_call(
            server, state, call_id, str(RalphToolName.READ_MEDIA), {"path": "report.pdf"}
        )

        assert result.get("isError") is not True, f"read_media returned error: {result}"
        content = cast("list[dict[str, Any]]", result.get("content", []))
        assert len(content) == 1
        block = content[0]
        assert block.get("type") == "resource_reference", (
            f"Expected resource_reference block, got type={block.get('type')!r}"
        )
        uri = str(block.get("uri", ""))
        assert uri.startswith("ralph://media/"), f"Expected ralph://media/ URI, got: {uri!r}"
        assert block.get("mimeType") == "application/pdf"
        assert block.get("modality") == "pdf"

        # Artifact must appear in resources/list
        list_req = JsonRpcRequest(jsonrpc="2.0", method="resources/list", params={}, msg_id=20)
        list_resp, _ = server.handle_request(list_req, state)
        assert list_resp is not None and list_resp.result is not None
        resources = cast(
            "list[dict[str, Any]]",
            cast("dict[str, Any]", list_resp.result).get("resources", []),
        )
        resource_uris = {r.get("uri") for r in resources}
        assert uri in resource_uris, f"URI {uri!r} not found in resources/list: {resource_uris}"

        # Artifact bytes must be retrievable via resources/read
        read_req = JsonRpcRequest(
            jsonrpc="2.0", method="resources/read", params={"uri": uri}, msg_id=21
        )
        read_resp, _ = server.handle_request(read_req, state)
        assert read_resp is not None and read_resp.result is not None, (
            f"resources/read failed: {read_resp}"
        )
        contents = cast(
            "list[dict[str, Any]]",
            cast("dict[str, Any]", read_resp.result).get("contents", []),
        )
        assert len(contents) == 1
        assert contents[0].get("uri") == uri
        assert contents[0].get("mimeType") == "application/pdf"
        assert isinstance(contents[0].get("blob"), str) and len(contents[0]["blob"]) > 0

    def test_read_media_audio_resource_reference_is_retrievable(self, tmp_path: Path) -> None:
        """Audio from unknown provider stored as resource_reference fetchable via resources/read."""

        mp3_bytes = b"ID3" + b"\x00" * 50
        with tempfile.NamedTemporaryFile(suffix=".mp3", dir=tmp_path, delete=False) as f:
            f.write(mp3_bytes)
            audio_name = Path(f.name).name

        server = _build_multimodal_server(tmp_path, provider="unknown-provider")
        state = _initialize_multimodal(server)

        call_id = [30]
        result = _do_tool_call(
            server, state, call_id, str(RalphToolName.READ_MEDIA), {"path": audio_name}
        )

        assert result.get("isError") is not True, f"read_media returned error: {result}"
        content = cast("list[dict[str, Any]]", result.get("content", []))
        assert len(content) == 1
        block = content[0]
        assert block.get("type") == "resource_reference", (
            f"Expected resource_reference for audio, got type={block.get('type')!r}"
        )
        assert block.get("modality") == "audio", (
            f"Expected audio modality, got: {block.get('modality')!r}"
        )
        uri = str(block.get("uri", ""))
        assert uri.startswith("ralph://media/"), f"Expected ralph://media/ URI: {uri!r}"

        # Artifact must be retrievable via resources/read
        read_req = JsonRpcRequest(
            jsonrpc="2.0",
            method="resources/read",
            params={"uri": uri},
            msg_id=31,
        )
        read_resp, _ = server.handle_request(read_req, state)
        assert read_resp is not None and read_resp.result is not None, (
            f"resources/read failed for audio: {read_resp}"
        )
        contents = cast(
            "list[dict[str, Any]]",
            cast("dict[str, Any]", read_resp.result).get("contents", []),
        )
        assert len(contents) == 1
        assert contents[0].get("uri") == uri
        assert isinstance(contents[0].get("blob"), str) and len(contents[0]["blob"]) > 0

    def test_read_media_video_resource_reference_is_retrievable(self, tmp_path: Path) -> None:
        """Video from unknown provider stored as resource_reference fetchable via resources/read."""

        mp4_bytes = b"\x00\x00\x00\x20ftyp" + b"\x00" * 40
        with tempfile.NamedTemporaryFile(suffix=".mp4", dir=tmp_path, delete=False) as f:
            f.write(mp4_bytes)
            video_name = Path(f.name).name

        server = _build_multimodal_server(tmp_path, provider="unknown-provider")
        state = _initialize_multimodal(server)

        call_id = [40]
        result = _do_tool_call(
            server, state, call_id, str(RalphToolName.READ_MEDIA), {"path": video_name}
        )

        assert result.get("isError") is not True, f"read_media returned error: {result}"
        content = cast("list[dict[str, Any]]", result.get("content", []))
        assert len(content) == 1
        block = content[0]
        assert block.get("type") == "resource_reference", (
            f"Expected resource_reference for video, got type={block.get('type')!r}"
        )
        assert block.get("modality") == "video", (
            f"Expected video modality, got: {block.get('modality')!r}"
        )
        uri = str(block.get("uri", ""))
        assert uri.startswith("ralph://media/"), f"Expected ralph://media/ URI: {uri!r}"

        read_req = JsonRpcRequest(
            jsonrpc="2.0",
            method="resources/read",
            params={"uri": uri},
            msg_id=41,
        )
        read_resp, _ = server.handle_request(read_req, state)
        assert read_resp is not None and read_resp.result is not None, (
            f"resources/read failed for video: {read_resp}"
        )
        contents = cast(
            "list[dict[str, Any]]",
            cast("dict[str, Any]", read_resp.result).get("contents", []),
        )
        assert len(contents) == 1
        assert contents[0].get("uri") == uri
        assert isinstance(contents[0].get("blob"), str) and len(contents[0]["blob"]) > 0

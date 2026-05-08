"""Integration tests for MCP wire-level roundtrip.

- HTTP transport tests: replaced with in-process McpServer.handle_request() calls —
  no daemon threads, no socket polling, no httpx against localhost.
- stdio transport: uses the fake stdio server fixture to test Ralph's MCP client.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import TYPE_CHECKING, Any, cast
from unittest.mock import MagicMock, patch

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.runtime import (
    JsonRpcRequest,
    McpServer,
    ServerState,
    build_ralph_tool_registry,
)
from ralph.mcp.tools.names import RalphToolName
from ralph.mcp.upstream.client import make_upstream_client
from ralph.mcp.upstream.config import UpstreamMcpServer
from ralph.mcp.webvisit.extractor import ExtractedPage
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from collections.abc import Generator

# Description length bounds enforced by the quality bar
_MIN_DESCRIPTION_CHARS = 20
_MAX_DESCRIPTION_CHARS = 500

# Content used by the append_file roundtrip assertion
_APPEND_CONTENT = "hi"

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


def _do_tools_list(server: McpServer, state: ServerState) -> list[dict[str, Any]]:
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
) -> dict[str, Any]:
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


@pytest.mark.integration
class TestHttpMcpServer:
    """Test the Ralph MCP server by driving it in-process via handle_request().

    No daemon threads, no socket polling, no HTTP transport overhead.
    """

    def test_initialize_tools_list_read_file_roundtrip(self, temp_workspace: Path) -> None:
        """Full MCP roundtrip exercising initialize, tools/list, and tools/call."""
        server = _build_server(temp_workspace)
        state = _do_initialize(server)
        tools = _do_tools_list(server, state)

        tool_names = {t["name"] for t in tools}
        assert "read_file" in tool_names
        assert "write_file" in tool_names
        assert "list_directory" in tool_names
        assert "exec" in tool_names
        assert "ralph_submit_artifact" in tool_names
        assert "visit_url" in tool_names

        _assert_tool_descriptions(tools)
        _do_read_file_test(server, state)

    def test_new_workspace_tools_roundtrip(self, temp_workspace: Path) -> None:
        """In-process roundtrip for the expanded workspace tool surface.

        Exercises stat_path, list_allowed_roots, read_multiple_files, directory_tree,
        search_files, grep_files, edit_file (dry_run + apply), append_file,
        create_directory, move_file, copy_file, and delete_path end-to-end.
        """
        _seed_extended_workspace(temp_workspace)

        server = _build_server(
            temp_workspace, session_id="test-new-tools", drain="development"
        )
        state = _do_initialize(server)
        tools = _do_tools_list(server, state)
        _assert_new_workspace_tools_present(tools)
        call_id = [10]
        _do_workspace_read_roundtrips(server, state, call_id)
        _do_workspace_write_roundtrips(server, state, call_id)

    def test_visit_url_tools_call(self, temp_workspace: Path) -> None:
        """tools/call for visit_url returns expected JSON shape (network mocked)."""
        server = _build_server(temp_workspace)
        state = _do_initialize(server)
        _do_tools_list(server, state)

        mock_extracted_page = ExtractedPage(
            title="Example Page",
            text="Test content",
            links=("https://example.com/link1",),
        )

        with (
            patch("ralph.mcp.webvisit.fetcher.httpx") as mock_httpx,
            patch(
                "ralph.mcp.tools.webvisit.extract_readable",
                return_value=mock_extracted_page,
            ),
        ):
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.url = "https://example.com/page"
            mock_response.headers = {"content-type": "text/html; charset=utf-8"}
            mock_response.iter_bytes.return_value = [
                b"<html><body><p>Test content</p></body></html>"
            ]
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)

            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value.__enter__ = MagicMock(return_value=mock_response)
            mock_client.stream.return_value.__exit__ = MagicMock(return_value=False)

            mock_httpx.Client.return_value.__enter__ = MagicMock(return_value=mock_client)
            mock_httpx.Client.return_value.__exit__ = MagicMock(return_value=False)
            mock_httpx.Client.return_value.stream.return_value.__enter__ = MagicMock(
                return_value=mock_response
            )
            mock_httpx.Client.return_value.stream.return_value.__exit__ = MagicMock(
                return_value=False
            )

            call_id = [3]
            result = _do_tool_call(
                server, state, call_id, "visit_url", {"url": "https://example.com/page"}
            )

        assert result.get("isError") is not True, f"visit_url returned error: {result}"
        content = result.get("content", [])
        assert len(content) >= 1
        text_block = content[0]
        assert text_block.get("type") == "text"
        inner = json.loads(text_block["text"])
        assert inner.get("status") == "ok"
        assert inner.get("title") == "Example Page"
        assert inner.get("effective_url") == "https://example.com/page"
        assert "Test content" in inner.get("text", "")


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary workspace with a test file."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_file = workspace / "test_read.txt"
    test_file.write_text("Hello, World!")
    yield workspace


def _seed_extended_workspace(workspace: Path) -> None:
    """Seed extra files into workspace for the new-tools roundtrip test."""
    (workspace / "src").mkdir()
    (workspace / "src" / "main.py").write_text("# main\ndef main():\n    print('Hello')\n")
    (workspace / "src" / "util.py").write_text("# util\ndef util():\n    pass\n")
    (workspace / "README.md").write_text("# Project\nHello World\n")


def _assert_new_workspace_tools_present(tools: list[dict[str, Any]]) -> None:
    """Assert that all new workspace tools appear in the tools/list response."""
    tool_names = {t["name"] for t in tools}
    expected = {
        str(RalphToolName.STAT_PATH),
        str(RalphToolName.LIST_ALLOWED_ROOTS),
        str(RalphToolName.READ_MULTIPLE_FILES),
        str(RalphToolName.DIRECTORY_TREE),
        str(RalphToolName.SEARCH_FILES),
        str(RalphToolName.GREP_FILES),
        str(RalphToolName.EDIT_FILE),
        str(RalphToolName.APPEND_FILE),
        str(RalphToolName.CREATE_DIRECTORY),
        str(RalphToolName.MOVE_FILE),
        str(RalphToolName.COPY_FILE),
        str(RalphToolName.DELETE_PATH),
    }
    for tool in expected:
        assert tool in tool_names, f"Tool {tool!r} missing from tools/list"


def _do_workspace_read_roundtrips(
    server: McpServer, state: ServerState, call_id: list[int]
) -> None:
    """Assert read-only workspace tool calls return expected shapes."""
    result = _do_tool_call(
        server, state, call_id, str(RalphToolName.STAT_PATH), {"path": "test_read.txt"}
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    assert inner.get("type") == "file"
    assert isinstance(inner.get("size_bytes"), int) and inner["size_bytes"] > 0

    result = _do_tool_call(server, state, call_id, str(RalphToolName.LIST_ALLOWED_ROOTS), {})
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    assert len(inner.get("allowed_roots", [])) > 0

    result = _do_tool_call(
        server,
        state,
        call_id,
        str(RalphToolName.READ_MULTIPLE_FILES),
        {"paths": ["test_read.txt", "missing_file.txt"]},
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    files = inner.get("files", [])
    assert any(f.get("path") == "test_read.txt" and "content" in f for f in files)
    assert any(f.get("path") == "missing_file.txt" and "error" in f for f in files)

    result = _do_tool_call(
        server,
        state,
        call_id,
        str(RalphToolName.DIRECTORY_TREE),
        {"path": ".", "max_depth": 1},
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    assert inner.get("type") == "dir" and isinstance(inner.get("children"), list)

    result = _do_tool_call(
        server,
        state,
        call_id,
        str(RalphToolName.SEARCH_FILES),
        {"pattern": "**/*.py", "path": "src"},
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    match_names = [m.split("/")[-1] for m in inner.get("matches", [])]
    assert "main.py" in match_names and "util.py" in match_names

    result = _do_tool_call(
        server,
        state,
        call_id,
        str(RalphToolName.GREP_FILES),
        {"pattern": "Hello", "path": "."},
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    assert len(inner.get("matches", [])) > 0
    first_match = inner["matches"][0]
    assert "line" in first_match and "text" in first_match


def _do_workspace_write_roundtrips(
    server: McpServer, state: ServerState, call_id: list[int]
) -> None:
    """Assert write/mutate workspace tool calls return expected shapes."""
    result = _do_tool_call(
        server, state, call_id, str(RalphToolName.CREATE_DIRECTORY), {"path": "newdir"}
    )
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]).get("created") is True

    result = _do_tool_call(
        server,
        state,
        call_id,
        str(RalphToolName.APPEND_FILE),
        {"path": "newdir/log.txt", "content": _APPEND_CONTENT},
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    assert inner.get("bytes_appended") == len(_APPEND_CONTENT)

    result = _do_tool_call(
        server,
        state,
        call_id,
        str(RalphToolName.EDIT_FILE),
        {
            "path": "test_read.txt",
            "edits": [{"oldText": "Hello", "newText": "Howdy"}],
            "dry_run": True,
        },
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    assert inner.get("status") == "preview" and "diff" in inner

    result = _do_tool_call(
        server,
        state,
        call_id,
        str(RalphToolName.EDIT_FILE),
        {
            "path": "test_read.txt",
            "edits": [{"oldText": "Hello", "newText": "Howdy"}],
            "dry_run": False,
        },
    )
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]).get("status") == "applied"

    result = _do_tool_call(
        server,
        state,
        call_id,
        str(RalphToolName.EDIT_FILE),
        {
            "path": "test_read.txt",
            "edits": [{"oldText": "missing token", "newText": "replaced"}],
            "dry_run": False,
        },
    )
    assert result.get("isError") is True
    inner = json.loads(result["content"][0]["text"])
    assert inner.get("status") == "no_match"
    assert "edit_index" in inner

    result = _do_tool_call(
        server,
        state,
        call_id,
        str(RalphToolName.MOVE_FILE),
        {"src": "newdir/log.txt", "dest": "newdir/log2.txt"},
    )
    assert result.get("isError") is not True

    result = _do_tool_call(
        server,
        state,
        call_id,
        str(RalphToolName.COPY_FILE),
        {"src": "newdir/log2.txt", "dest": "newdir/log3.txt"},
    )
    assert result.get("isError") is not True

    result = _do_tool_call(
        server, state, call_id, str(RalphToolName.DELETE_PATH), {"path": "newdir/log3.txt"}
    )
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]).get("deleted") is True

    result = _do_tool_call(
        server,
        state,
        call_id,
        str(RalphToolName.DELETE_PATH),
        {"path": "newdir", "recursive": True},
    )
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]).get("deleted") is True


def _assert_tool_descriptions(tools: list[dict[str, Any]]) -> None:
    """Assert every tool description meets the quality bar."""
    for tool in tools:
        desc = tool.get("description", "")
        assert len(desc) >= _MIN_DESCRIPTION_CHARS, (
            f"Tool {tool['name']} description too short: {desc!r}"
        )
        assert len(desc) <= _MAX_DESCRIPTION_CHARS, (
            f"Tool {tool['name']} description too long: {desc!r}"
        )
        assert tool.get("inputSchema", {}).get("type") == "object", (
            f"Tool {tool['name']} inputSchema type is not 'object': "
            f"{tool.get('inputSchema')}"
        )


def _do_read_file_test(server: McpServer, state: ServerState) -> None:
    """Call tools/call for read_file and verify the seeded content is returned."""
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={"name": "read_file", "arguments": {"path": "test_read.txt"}},
        msg_id=3,
    )
    resp, _ = server.handle_request(req, state)
    assert resp is not None and resp.result is not None, "read_file call failed"
    result = cast("dict[str, Any]", resp.result)
    assert result.get("isError") is False
    content = result.get("content", [])
    assert any(
        block.get("type") == "text" and "Hello, World!" in block.get("text", "")
        for block in content
    ), f"Expected 'Hello, World!' in read_file result, got: {content}"


@pytest.mark.integration
@pytest.mark.subprocess_e2e
class TestStdioUpstreamClient:
    """Test Ralph's MCP client code path using the fake stdio fixture."""

    def test_list_tools_from_fake_stdio_server(self) -> None:
        """make_upstream_client lists tools from the fake stdio server."""
        fake_stdio_path = Path(__file__).parent.parent / "fixtures" / "fake_stdio_mcp.py"

        server = UpstreamMcpServer(
            name="fake",
            transport="stdio",
            command=sys.executable,
            args=(str(fake_stdio_path),),
        )

        client = make_upstream_client(server)
        tools = client.list_tools()

        assert len(tools) == 1
        assert tools[0].name == "fake_tool"
        assert tools[0].description == "A fake tool for testing"

    def test_call_tool_on_fake_stdio_server(self) -> None:
        """call_tool on the fake stdio server returns the expected response shape."""
        fake_stdio_path = Path(__file__).parent.parent / "fixtures" / "fake_stdio_mcp.py"

        server = UpstreamMcpServer(
            name="fake",
            transport="stdio",
            command=sys.executable,
            args=(str(fake_stdio_path),),
        )

        client = make_upstream_client(server)
        result = client.call_tool("fake_tool", {})

        assert result is not None
        as_dict = getattr(result, "to_dict", None)
        result_dict = as_dict() if as_dict else result
        assert isinstance(result_dict, dict)
        content = result_dict.get("content", [])
        assert isinstance(content, list)
        assert len(content) == 1
        assert content[0].get("type") == "text"
        assert content[0].get("text") == "fake-result"
        assert result_dict.get("isError") is False


# ---------------------------------------------------------------------------
# Multimodal tool roundtrip tests (Step 3)
# ---------------------------------------------------------------------------

# Minimal valid 1x1 PNG (67 bytes) — no external file needed.
_TINY_PNG_BYTES = bytes([
    0x89, 0x50, 0x4E, 0x47, 0x0D, 0x0A, 0x1A, 0x0A,  # PNG signature
    0x00, 0x00, 0x00, 0x0D, 0x49, 0x48, 0x44, 0x52,  # IHDR chunk length + type
    0x00, 0x00, 0x00, 0x01, 0x00, 0x00, 0x00, 0x01,  # width=1, height=1
    0x08, 0x02, 0x00, 0x00, 0x00, 0x90, 0x77, 0x53,  # bit depth, color type, etc.
    0xDE, 0x00, 0x00, 0x00, 0x0C, 0x49, 0x44, 0x41,  # IDAT chunk
    0x54, 0x08, 0xD7, 0x63, 0xF8, 0xCF, 0xC0, 0x00,
    0x00, 0x00, 0x02, 0x00, 0x01, 0xE2, 0x21, 0xBC,
    0x33, 0x00, 0x00, 0x00, 0x00, 0x49, 0x45, 0x4E,  # IEND chunk
    0x44, 0xAE, 0x42, 0x60, 0x82,
])

# Minimal PDF header bytes — sufficient to pass MIME routing.
_TINY_PDF_BYTES = b"%PDF-1.4\n1 0 obj\n<< /Type /Catalog >>\nendobj\n%%EOF\n"

_MULTIMODAL_CAPABILITIES = _REQUIRED_CAPABILITIES | {"media.read"}


def _build_multimodal_server(
    workspace_path: Path,
    *,
    session_id: str = "test-media",
    provider: str = "claude",
    model_id: str = "claude-3-5-sonnet-20241022",
) -> McpServer:
    """Build a McpServer with media.read capability and Claude model identity."""
    from ralph.mcp.multimodal.capabilities import MultimodalModelIdentity  # noqa: PLC0415

    workspace = FsWorkspace(workspace_path)
    session = AgentSession(
        session_id=session_id,
        run_id="test-run",
        drain="development",
        capabilities=_MULTIMODAL_CAPABILITIES,
        model_identity=MultimodalModelIdentity(
            provider=provider,
            model_id=model_id,
        ),
    )
    registry = build_ralph_tool_registry(session, workspace)
    return McpServer(session, workspace, registry)


def _initialize_multimodal(server: McpServer) -> ServerState:
    """Send initialize with multimodal client capabilities; return running state."""
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="initialize",
        params={
            "protocolVersion": "2024-11-05",
            "capabilities": {"media": {}, "image": {}},
            "clientInfo": {"name": "test", "version": "1.0"},
        },
        msg_id=1,
    )
    resp, state = server.handle_request(req, ServerState.UNINITIALIZED)
    assert resp is not None and resp.result is not None
    notif = JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", params={})
    none_resp, state = server.handle_request(notif, state)
    assert none_resp is None
    return state


@pytest.mark.integration
class TestMultimodalToolRoundtrip:
    """Black-box multimodal tool/resource roundtrips via McpServer.handle_request()."""

    def test_read_media_png_with_claude_session_returns_inline_image(
        self, tmp_path: Path
    ) -> None:
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
        list_req = JsonRpcRequest(
            jsonrpc="2.0", method="resources/list", params={}, msg_id=20
        )
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

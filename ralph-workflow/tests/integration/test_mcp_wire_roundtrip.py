"""Integration tests for MCP wire-level roundtrip over HTTP and stdio.

These tests exercise the full MCP server and client code paths:
- HTTP transport: boots a real Ralph MCP HTTP server and walks initialize→tools/list→tools/call
- stdio transport: uses the fake stdio server fixture to test Ralph's MCP client
"""

from __future__ import annotations

import json
import socket
import sys
import threading
import time
from http import HTTPStatus
from pathlib import Path
from typing import TYPE_CHECKING, Any
from unittest.mock import MagicMock, patch

import httpx
import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.runtime import (
    McpServer,
    _FallbackStandaloneServer,
)
from ralph.mcp.tools.bridge import build_ralph_tool_registry
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

# Server startup timeout
_SERVER_START_TIMEOUT = 10.0

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


@pytest.mark.integration
@pytest.mark.timeout_seconds(30)
class TestHttpMcpServer:
    """Test the Ralph MCP HTTP server by booting it and walking the JSON-RPC handshake."""

    def test_initialize_tools_list_read_file_roundtrip(self, temp_workspace: Path) -> None:
        """Full MCP wire roundtrip over HTTP transport.

        Exercises the public runtime entrypoint and JSON-RPC handshake over HTTP.
        Uses port-0 bind so the OS assigns an available ephemeral port.
        """
        workspace = FsWorkspace(temp_workspace)
        session = AgentSession(
            session_id="test-session",
            run_id="test-run",
            drain="test",
            capabilities=_REQUIRED_CAPABILITIES,
        )
        registry = build_ralph_tool_registry(session, workspace)
        mcp_server = McpServer(session, workspace, registry)

        # Use _FallbackStandaloneServer to properly wire httpd.mcp_server
        standalone = _FallbackStandaloneServer("127.0.0.1", 0, mcp_server)

        ready_event = threading.Event()
        server_port: dict[str, int] = {}

        def run_server() -> None:
            # Start the server (this sets httpd.mcp_server)
            # _FallbackStandaloneServer.run() blocks, so run in thread
            standalone.run("streamable-http")
            # This never returns until shutdown

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()

        # Wait for _httpd to be set and port to be bound
        deadline = time.monotonic() + _SERVER_START_TIMEOUT
        while time.monotonic() < deadline:
            if standalone._httpd is not None:
                port = standalone._httpd.server_address[1]
                server_port["port"] = port
                try:
                    _wait_for_port(port, timeout=1.0)
                    ready_event.set()
                    break
                except AssertionError:
                    pass
            time.sleep(0.05)

        if not ready_event.is_set():
            raise AssertionError("Server did not start within 10 seconds")

        port = server_port["port"]
        base_url = f"http://127.0.0.1:{port}/mcp"

        try:
            session_id = _do_initialize(base_url)
            _do_initialized_notification(base_url, session_id)
            tools = _do_tools_list(base_url, session_id)

            tool_names = {t["name"] for t in tools}
            assert "read_file" in tool_names
            assert "write_file" in tool_names
            assert "list_directory" in tool_names
            assert "exec" in tool_names
            assert "ralph_submit_artifact" in tool_names
            assert "visit_url" in tool_names

            _assert_tool_descriptions(tools)

            _do_read_file_test(base_url, session_id)
        finally:
            # Explicitly close server socket before shutdown to avoid socket leaks
            httpd = standalone._httpd
            if httpd is not None:
                httpd.server_close()
                httpd.shutdown()
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise AssertionError("Server thread did not shut down within 5 seconds")

    def test_new_workspace_tools_roundtrip(self, temp_workspace: Path) -> None:
        """Wire-level roundtrip for the expanded workspace tool surface.

        Boots one server and exercises stat_path, list_allowed_roots,
        read_multiple_files, directory_tree, search_files, grep_files,
        edit_file (dry_run + apply), append_file, create_directory,
        move_file, copy_file, and delete_path end-to-end over JSON-RPC.
        """
        _seed_extended_workspace(temp_workspace)

        workspace = FsWorkspace(temp_workspace)
        session = AgentSession(
            session_id="test-new-tools",
            run_id="test-run-new",
            drain="development",
            capabilities=_REQUIRED_CAPABILITIES,
        )
        registry = build_ralph_tool_registry(session, workspace)
        mcp_server = McpServer(session, workspace, registry)
        standalone = _FallbackStandaloneServer("127.0.0.1", 0, mcp_server)
        server_port: dict[str, int] = {}

        def run_server() -> None:
            standalone.run("streamable-http")

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()

        started = False
        deadline = time.monotonic() + _SERVER_START_TIMEOUT
        while time.monotonic() < deadline:
            if standalone._httpd is not None:
                port = standalone._httpd.server_address[1]
                server_port["port"] = port
                try:
                    _wait_for_port(port, timeout=1.0)
                    started = True
                    break
                except AssertionError:
                    pass
            time.sleep(0.05)

        if not started:
            raise AssertionError("Server did not start within 10 seconds")

        base_url = f"http://127.0.0.1:{server_port['port']}/mcp"

        try:
            session_id = _do_initialize(base_url)
            _do_initialized_notification(base_url, session_id)
            tools = _do_tools_list(base_url, session_id)
            _assert_new_workspace_tools_present(tools)
            call_id = [10]
            _do_workspace_read_roundtrips(base_url, session_id, call_id)
            _do_workspace_write_roundtrips(base_url, session_id, call_id)
        finally:
            httpd = standalone._httpd
            if httpd is not None:
                httpd.server_close()
                httpd.shutdown()
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise AssertionError("Server thread did not shut down within 5 seconds")

    def test_visit_url_tools_call_over_http(self, temp_workspace: Path) -> None:
        """Wire-level test: tools/call for visit_url returns expected JSON shape."""
        workspace = FsWorkspace(temp_workspace)
        session = AgentSession(
            session_id="test-session",
            run_id="test-run",
            drain="test",
            capabilities=_REQUIRED_CAPABILITIES,
        )
        registry = build_ralph_tool_registry(session, workspace)
        mcp_server = McpServer(session, workspace, registry)

        standalone = _FallbackStandaloneServer("127.0.0.1", 0, mcp_server)

        ready_event = threading.Event()
        server_port: dict[str, int] = {}

        def run_server() -> None:
            standalone.run("streamable-http")

        thread = threading.Thread(target=run_server, daemon=True)
        thread.start()

        deadline = time.monotonic() + _SERVER_START_TIMEOUT
        while time.monotonic() < deadline:
            if standalone._httpd is not None:
                port = standalone._httpd.server_address[1]
                server_port["port"] = port
                try:
                    _wait_for_port(port, timeout=1.0)
                    ready_event.set()
                    break
                except AssertionError:
                    pass
            time.sleep(0.05)

        if not ready_event.is_set():
            raise AssertionError("Server did not start within 10 seconds")

        port = server_port["port"]
        base_url = f"http://127.0.0.1:{port}/mcp"

        # Mock fetch_url and extract_readable so no real network IO occurs
        mock_extracted_page = ExtractedPage(
            title="Example Page",
            text="Test content",
            links=("https://example.com/link1",),
        )

        try:
            self._do_visit_url_mocked_test(
                base_url, mock_extracted_page, temp_workspace
            )
        finally:
            httpd = standalone._httpd
            if httpd is not None:
                httpd.server_close()
                httpd.shutdown()
            thread.join(timeout=5.0)
            if thread.is_alive():
                raise AssertionError("Server thread did not shut down within 5 seconds")

    def _do_visit_url_mocked_test(
        self, base_url: str, mock_extracted_page: ExtractedPage, workspace: Path
    ) -> None:
        """Helper that performs the mocked visit_url tools/call assertions."""
        with (
            patch("ralph.mcp.webvisit.fetcher.httpx") as mock_httpx,
            patch(
                "ralph.mcp.tools.webvisit.extract_readable",
                return_value=mock_extracted_page,
            ),
        ):
            # Build a mock response for httpx.Client context manager
            mock_response = MagicMock()
            mock_response.status_code = 200
            mock_response.url = "https://example.com/page"
            mock_response.headers = {
                "content-type": "text/html; charset=utf-8"
            }
            mock_response.iter_bytes.return_value = [
                b"<html><body><p>Test content</p></body></html>"
            ]
            mock_response.__enter__ = MagicMock(return_value=mock_response)
            mock_response.__exit__ = MagicMock(return_value=False)

            mock_client = MagicMock()
            mock_client.__enter__ = MagicMock(return_value=mock_client)
            mock_client.__exit__ = MagicMock(return_value=False)
            mock_client.stream.return_value.__enter__ = MagicMock(
                return_value=mock_response
            )
            mock_client.stream.return_value.__exit__ = MagicMock(
                return_value=False
            )

            mock_httpx.Client.return_value.__enter__ = MagicMock(
                return_value=mock_client
            )
            mock_httpx.Client.return_value.__exit__ = MagicMock(
                return_value=False
            )
            mock_httpx.Client.return_value.stream.return_value.__enter__ = (
                MagicMock(return_value=mock_response)
            )
            mock_httpx.Client.return_value.stream.return_value.__exit__ = (
                MagicMock(return_value=False)
            )

            session_id = _do_initialize(base_url)
            _do_initialized_notification(base_url, session_id)
            _do_tools_list(base_url, session_id)

            # Call tools/call for visit_url
            payload = {
                "jsonrpc": "2.0",
                "id": 4,
                "method": "tools/call",
                "params": {
                    "name": "visit_url",
                    "arguments": {"url": "https://example.com/page"},
                },
            }
            with httpx.Client(timeout=10.0) as client:
                response = client.post(
                    base_url,
                    json=payload,
                    headers={"Mcp-Session-Id": session_id},
                )

            assert response.status_code == HTTPStatus.OK.value
            call_data = _parse_sse_body(response.content)
            assert "result" in call_data, f"tools/call failed: {call_data}"
            result = call_data["result"]

            # Should not be an error (fetch succeeds with mocked response)
            assert result.get("isError") is not True, (
                f"visit_url returned error: {result}"
            )

            content = result.get("content", [])
            assert len(content) >= 1
            text_block = content[0]
            assert text_block.get("type") == "text"

            # Parse the JSON text content
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


def _do_tool_call(
    base_url: str,
    session_id: str,
    call_id: list[int],
    name: str,
    args: dict[str, object],
) -> dict[str, Any]:
    """Make a tools/call JSON-RPC request and return the MCP result object."""
    call_id[0] += 1
    payload = {
        "jsonrpc": "2.0",
        "id": call_id[0],
        "method": "tools/call",
        "params": {"name": name, "arguments": args},
    }
    with httpx.Client(timeout=10.0) as client:
        resp = client.post(
            base_url,
            json=payload,
            headers={"Mcp-Session-Id": session_id},
        )
    assert resp.status_code == HTTPStatus.OK.value, (
        f"tools/call {name} HTTP {resp.status_code}"
    )
    data = _parse_sse_body(resp.content)
    assert "result" in data, f"tools/call {name} failed: {data}"
    return data["result"]


def _do_workspace_read_roundtrips(
    base_url: str, session_id: str, call_id: list[int]
) -> None:
    """Assert read-only workspace tool calls return expected shapes."""
    result = _do_tool_call(
        base_url, session_id, call_id, str(RalphToolName.STAT_PATH), {"path": "test_read.txt"}
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    assert inner.get("type") == "file"
    assert isinstance(inner.get("size_bytes"), int) and inner["size_bytes"] > 0

    result = _do_tool_call(base_url, session_id, call_id, str(RalphToolName.LIST_ALLOWED_ROOTS), {})
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    assert len(inner.get("allowed_roots", [])) > 0

    result = _do_tool_call(
        base_url, session_id, call_id, str(RalphToolName.READ_MULTIPLE_FILES),
        {"paths": ["test_read.txt", "missing_file.txt"]},
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    files = inner.get("files", [])
    assert any(f.get("path") == "test_read.txt" and "content" in f for f in files)
    assert any(f.get("path") == "missing_file.txt" and "error" in f for f in files)

    result = _do_tool_call(
        base_url, session_id, call_id,
        str(RalphToolName.DIRECTORY_TREE), {"path": ".", "max_depth": 1},
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    assert inner.get("type") == "dir" and isinstance(inner.get("children"), list)

    result = _do_tool_call(
        base_url, session_id, call_id, str(RalphToolName.SEARCH_FILES),
        {"pattern": "**/*.py", "path": "src"},
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    match_names = [m.split("/")[-1] for m in inner.get("matches", [])]
    assert "main.py" in match_names and "util.py" in match_names

    result = _do_tool_call(
        base_url, session_id, call_id,
        str(RalphToolName.GREP_FILES), {"pattern": "Hello", "path": "."},
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    assert len(inner.get("matches", [])) > 0
    first_match = inner["matches"][0]
    assert "line" in first_match and "text" in first_match


def _do_workspace_write_roundtrips(
    base_url: str, session_id: str, call_id: list[int]
) -> None:
    """Assert write/mutate workspace tool calls return expected shapes."""
    result = _do_tool_call(
        base_url, session_id, call_id, str(RalphToolName.CREATE_DIRECTORY), {"path": "newdir"}
    )
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]).get("created") is True

    result = _do_tool_call(
        base_url, session_id, call_id, str(RalphToolName.APPEND_FILE),
        {"path": "newdir/log.txt", "content": _APPEND_CONTENT},
    )
    assert result.get("isError") is not True
    inner = json.loads(result["content"][0]["text"])
    assert inner.get("bytes_appended") == len(_APPEND_CONTENT)

    result = _do_tool_call(
        base_url, session_id, call_id, str(RalphToolName.EDIT_FILE),
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
        base_url, session_id, call_id, str(RalphToolName.EDIT_FILE),
        {
            "path": "test_read.txt",
            "edits": [{"oldText": "Hello", "newText": "Howdy"}],
            "dry_run": False,
        },
    )
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]).get("status") == "applied"

    result = _do_tool_call(
        base_url, session_id, call_id, str(RalphToolName.EDIT_FILE),
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
        base_url, session_id, call_id, str(RalphToolName.MOVE_FILE),
        {"src": "newdir/log.txt", "dest": "newdir/log2.txt"},
    )
    assert result.get("isError") is not True

    result = _do_tool_call(
        base_url, session_id, call_id, str(RalphToolName.COPY_FILE),
        {"src": "newdir/log2.txt", "dest": "newdir/log3.txt"},
    )
    assert result.get("isError") is not True

    result = _do_tool_call(
        base_url, session_id, call_id, str(RalphToolName.DELETE_PATH), {"path": "newdir/log3.txt"}
    )
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]).get("deleted") is True

    result = _do_tool_call(
        base_url, session_id, call_id, str(RalphToolName.DELETE_PATH),
        {"path": "newdir", "recursive": True},
    )
    assert result.get("isError") is not True
    assert json.loads(result["content"][0]["text"]).get("deleted") is True


def _parse_sse_body(body: bytes) -> dict[str, Any]:
    """Parse a server-sent events response body and extract the JSON data.

    SSE format: "event: message\\r\\ndata: {...JSON...}\\r\\n\\r\\n"
    """
    if not body:
        return {}
    text = body.decode("utf-8")
    marker = "data: "
    idx = text.find(marker)
    if idx == -1:
        return {}
    start = idx + len(marker)
    json_start = text.find("{", start)
    if json_start == -1:
        return {}
    depth = 0
    i = json_start
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                json_str = text[json_start : i + 1]
                return json.loads(json_str)
        i += 1
    return {}


def _get_header(headers: dict[str, str], key: str) -> str | None:
    """Get a header value case-insensitively."""
    lower_key = key.lower()
    for k, v in headers.items():
        if k.lower() == lower_key:
            return v
    return None


def _wait_for_port(port: int, timeout: float = 5.0) -> None:
    """Wait for a port to be accepting connections."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        sock.settimeout(0.1)
        try:
            sock.connect(("127.0.0.1", port))
            return
        except OSError:
            pass
        finally:
            sock.close()
        time.sleep(0.01)
    raise AssertionError(f"Port {port} never started accepting connections")


def _do_initialize(base_url: str) -> str:
    """Send initialize request and return the session ID."""
    payload = {
        "jsonrpc": "2.0",
        "id": 1,
        "method": "initialize",
        "params": {
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "test", "version": "1.0.00"},
        },
    }
    # Use a new client each time to avoid connection reuse issues
    with httpx.Client(timeout=10.0) as client:
        response = client.post(base_url, json=payload)
        status = response.status_code
        body = response.content
        headers = dict(response.headers)

    assert status == HTTPStatus.OK.value, f"initialize failed with {status}"
    init_data = _parse_sse_body(body)
    assert init_data.get("result"), f"initialize failed: {init_data}"
    assert init_data["result"]["protocolVersion"] == "2024-11-05"

    session_id = _get_header(headers, "mcp-session-id")
    assert session_id, "mcp-session-id header missing from initialize response"
    return session_id


def _do_initialized_notification(base_url: str, session_id: str) -> None:
    """Send notifications/initialized and expect 202 Accepted."""
    payload = {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}}
    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            base_url,
            json=payload,
            headers={"Mcp-Session-Id": session_id},
        )
        status = response.status_code

    assert status == HTTPStatus.ACCEPTED.value, (
        f"notifications/initialized failed with {status}"
    )


def _do_tools_list(base_url: str, session_id: str) -> list[dict[str, Any]]:
    """Send tools/list request and return the tools array."""
    payload = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            base_url,
            json=payload,
            headers={"Mcp-Session-Id": session_id},
        )
        status = response.status_code
        body = response.content

    assert status == HTTPStatus.OK.value, f"tools/list failed with {status}"
    list_data = _parse_sse_body(body)
    assert list_data, "tools/list returned no data"
    assert "result" in list_data, f"tools/list failed: {list_data}"
    return list_data["result"]["tools"]


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


def _do_read_file_test(base_url: str, session_id: str) -> None:
    """Send tools/call for read_file and verify the seeded content is returned."""
    payload = {
        "jsonrpc": "2.0",
        "id": 3,
        "method": "tools/call",
        "params": {"name": "read_file", "arguments": {"path": "test_read.txt"}},
    }
    with httpx.Client(timeout=10.0) as client:
        response = client.post(
            base_url,
            json=payload,
            headers={"Mcp-Session-Id": session_id},
        )
        status = response.status_code
        body = response.content

    assert status == HTTPStatus.OK.value, f"tools/call failed with {status}"
    call_data = _parse_sse_body(body)
    assert "result" in call_data, f"tools/call failed: {call_data}"
    result = call_data["result"]
    assert result.get("isError") is False
    content = result.get("content", [])
    assert any(
        block.get("type") == "text" and "Hello, World!" in block.get("text", "")
        for block in content
    ), f"Expected 'Hello, World!' in read_file result, got: {content}"


@pytest.mark.integration
@pytest.mark.timeout_seconds(30)
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

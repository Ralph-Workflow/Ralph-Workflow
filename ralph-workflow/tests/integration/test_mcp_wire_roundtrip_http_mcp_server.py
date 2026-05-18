"""Integration tests for MCP wire-level roundtrip.

- HTTP transport tests: replaced with in-process McpServer.handle_request() calls —
  no daemon threads, no socket polling, no httpx against localhost.
- stdio transport: uses the fake stdio server fixture to test Ralph's MCP client.
"""

from __future__ import annotations

import json
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
from ralph.mcp.webvisit.extractor import ExtractedPage
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.subprocess_e2e

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


def _assert_tool_descriptions(tools: list[dict[str, object]]) -> None:
    for tool in tools:
        name = tool.get("name", "")
        desc = tool.get("description", "")
        assert isinstance(desc, str) and len(desc) >= _MIN_DESCRIPTION_CHARS, (
            f"Tool {name!r} description too short: {len(desc)} chars"
        )
        assert len(desc) <= _MAX_DESCRIPTION_CHARS, (
            f"Tool {name!r} description too long: {len(desc)} chars"
        )


def _do_read_file_test(server: McpServer, state: ServerState) -> None:
    call_id = [99]
    _do_tool_call(
        server,
        state,
        call_id,
        "write_file",
        {"path": "_test_read_file.txt", "content": "hello roundtrip"},
    )
    result = _do_tool_call(server, state, call_id, "read_file", {"path": "_test_read_file.txt"})
    assert result.get("isError") is not True
    content = cast("list[dict[str, Any]]", result.get("content", []))
    assert any("hello roundtrip" in str(block.get("text", "")) for block in content)


def _seed_extended_workspace(workspace_path: Path) -> None:
    (workspace_path / "seed_dir").mkdir(exist_ok=True)
    (workspace_path / "seed_dir" / "hello.txt").write_text("hello world", encoding="utf-8")
    (workspace_path / "seed_file.txt").write_text("seed content", encoding="utf-8")


def _assert_new_workspace_tools_present(tools: list[dict[str, object]]) -> None:
    tool_names = {t["name"] for t in tools}
    for expected in (
        "stat_path",
        "list_allowed_roots",
        "read_multiple_files",
        "directory_tree",
        "search_files",
        "grep_files",
        "edit_file",
        "append_file",
        "create_directory",
        "move_file",
        "copy_file",
        "delete_path",
    ):
        assert expected in tool_names, f"Expected tool {expected!r} not found in tools/list"


def _do_workspace_read_roundtrips(
    server: McpServer, state: ServerState, call_id: list[int]
) -> None:
    result = _do_tool_call(server, state, call_id, "stat_path", {"path": "seed_file.txt"})
    assert result.get("isError") is not True, f"stat_path failed: {result}"
    result = _do_tool_call(server, state, call_id, "list_allowed_roots", {})
    assert result.get("isError") is not True, f"list_allowed_roots failed: {result}"
    result = _do_tool_call(server, state, call_id, "directory_tree", {"path": "."})
    assert result.get("isError") is not True, f"directory_tree failed: {result}"
    result = _do_tool_call(
        server, state, call_id, "search_files", {"pattern": "*.txt", "path": "."}
    )
    assert result.get("isError") is not True, f"search_files failed: {result}"
    result = _do_tool_call(server, state, call_id, "grep_files", {"pattern": "hello", "path": "."})
    assert result.get("isError") is not True, f"grep_files failed: {result}"
    result = _do_tool_call(
        server, state, call_id, "read_multiple_files", {"paths": ["seed_file.txt"]}
    )
    assert result.get("isError") is not True, f"read_multiple_files failed: {result}"


def _do_workspace_write_roundtrips(
    server: McpServer, state: ServerState, call_id: list[int]
) -> None:
    result = _do_tool_call(server, state, call_id, "create_directory", {"path": "new_dir"})
    assert result.get("isError") is not True, f"create_directory failed: {result}"
    _do_tool_call(server, state, call_id, "write_file", {"path": "src_file.txt", "content": "src"})
    result = _do_tool_call(
        server, state, call_id, "copy_file", {"src": "src_file.txt", "dest": "copied_file.txt"}
    )
    assert result.get("isError") is not True, f"copy_file failed: {result}"
    result = _do_tool_call(
        server, state, call_id, "move_file", {"src": "copied_file.txt", "dest": "moved_file.txt"}
    )
    assert result.get("isError") is not True, f"move_file failed: {result}"
    result = _do_tool_call(
        server,
        state,
        call_id,
        "append_file",
        {"path": "moved_file.txt", "content": _APPEND_CONTENT},
    )
    assert result.get("isError") is not True, f"append_file failed: {result}"
    result = _do_tool_call(
        server,
        state,
        call_id,
        "edit_file",
        {
            "path": "moved_file.txt",
            "edits": [{"oldText": "src", "newText": "edited"}],
            "dry_run": True,
        },
    )
    assert result.get("isError") is not True, f"edit_file dry_run failed: {result}"
    result = _do_tool_call(
        server,
        state,
        call_id,
        "edit_file",
        {"path": "moved_file.txt", "edits": [{"oldText": "src", "newText": "edited"}]},
    )
    assert result.get("isError") is not True, f"edit_file apply failed: {result}"
    result = _do_tool_call(server, state, call_id, "delete_path", {"path": "moved_file.txt"})
    assert result.get("isError") is not True, f"delete_path failed: {result}"


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

        server = _build_server(temp_workspace, session_id="test-new-tools", drain="development")
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

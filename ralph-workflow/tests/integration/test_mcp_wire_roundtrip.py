"""Integration tests for MCP wire-level roundtrip over HTTP and stdio.

These tests exercise MCP client code paths using fake stdio servers.
HTTP transport tests require more complex server lifecycle management
and are covered by the e2e tests in test_mcp_e2e.py.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from ralph.mcp.upstream.client import make_upstream_client
from ralph.mcp.upstream.config import UpstreamMcpServer

if TYPE_CHECKING:
    from collections.abc import Generator


@pytest.fixture
def temp_workspace(tmp_path: Path) -> Generator[Path, None, None]:
    """Create a temporary workspace with a test file."""
    workspace = tmp_path / "workspace"
    workspace.mkdir()
    test_file = workspace / "test_read.txt"
    test_file.write_text("Hello, World!")
    yield workspace


class TestStdioUpstreamClient:
    """Test Ralph's MCP client code path using the fake stdio fixture."""

    def test_list_tools_from_fake_stdio_server(
        self, temp_workspace: Path
    ) -> None:
        """make_upstream_client lists tools from the fake stdio server."""
        fake_stdio_path = (
            Path(__file__).parent.parent / "fixtures" / "fake_stdio_mcp.py"
        )

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

    def test_call_tool_on_fake_stdio_server(
        self, temp_workspace: Path
    ) -> None:
        """call_tool on the fake stdio server returns the expected response."""
        fake_stdio_path = (
            Path(__file__).parent.parent / "fixtures" / "fake_stdio_mcp.py"
        )

        server = UpstreamMcpServer(
            name="fake",
            transport="stdio",
            command=sys.executable,
            args=(str(fake_stdio_path),),
        )

        client = make_upstream_client(server)
        result = client.call_tool("fake_tool", {})

        assert result is not None

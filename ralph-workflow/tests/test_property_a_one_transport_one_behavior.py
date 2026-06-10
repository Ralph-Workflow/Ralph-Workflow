# property-test: A — one transport, one behavior, the shipped path is the tested path
"""The shipped path is the tested path.

The FastMCP-only async tool-offload path has been hard-deleted. This test
pins the absence of the alternate path and the fact that the production
transport is the one every behavioral test exercises.
"""

from __future__ import annotations

import importlib
import json
from pathlib import Path
from typing import cast

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import _fallback_http_handler
from ralph.mcp.server._in_memory_transport import drive_request, parse_sse_data
from ralph.mcp.server.runtime import McpServer, build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

REPO = Path("/Users/mistlight/Projects/Ralph-Workflow/wt-004-mcp-fixes/ralph-workflow")
FORBIDDEN_TOKENS = (
    "build_fastmcp_server",
    "_make_tool_metadata",
    "_create_tool",
    "ToolBuilderLike",
    "ToolManagerLike",
    "func_metadata",
    "anyio.to_thread",
    "FastMCP",
)
INCLUDED_SUFFIXES = (".py", ".md")


def _iter_source_files(root: Path) -> list[Path]:
    out: list[Path] = []
    for path in root.rglob("*"):
        if not path.is_file():
            continue
        if path.suffix not in INCLUDED_SUFFIXES:
            continue
        if any(part == "__pycache__" for part in path.parts):
            continue
        out.append(path)
    return out


def test_runtime_module_contains_no_fastmcp_symbols() -> None:
    """The runtime module must not export any FastMCP-only construction path."""
    text = (REPO / "ralph" / "mcp" / "server" / "runtime.py").read_text()
    for token in FORBIDDEN_TOKENS:
        assert token not in text, (
            f"runtime.py must not contain {token!r} after the FastMCP path was deleted"
        )


def test_grep_audit_finds_zero_fastmcp_hits_in_ralph() -> None:
    """The file-walk audit must find no hits in ralph/ outside the absence-asserting test."""
    hits: list[str] = []
    for path in _iter_source_files(REPO / "ralph"):
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in FORBIDDEN_TOKENS:
            if token in text:
                rel = path.relative_to(REPO)
                hits.append(f"{rel}: contains {token!r}")
    assert not hits, (
        f"file walk should find no FastMCP references in ralph/, got: {hits}"
    )


def test_in_memory_transport_drives_dispatch_via_saturated_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Drives the in-memory transport; verifies the dispatch offloads via the seam."""
    seen: list[object] = []
    monkeypatch.setattr(
        _fallback_http_handler._saturated_dispatch,
        "submit",
        lambda c: (seen.append(c), c())[1],
    )
    session = AgentSession(
        session_id="prop-a",
        run_id="prop-a-run",
        drain="standalone",
        capabilities={
            "WorkspaceRead",
            "ArtifactSubmit",
            "RunReportProgress",
        },
    )
    workspace = FsWorkspace(tmp_path)
    registry = build_ralph_tool_registry(session, workspace)
    mcp_server = McpServer(session, workspace, registry)
    payload = json.dumps(
        {"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    ).encode()
    status, _headers, body = drive_request(mcp_server, payload)
    assert status == 200
    assert seen, "the dispatch must run through the saturated-dispatch seam"
    data = parse_sse_data(body)
    result = cast("dict[str, object]", data.get("result", {}))
    assert "tools" in result


def test_ralph_mcp_server_public_surface_has_no_fastmcp_symbol() -> None:
    """The ralph.mcp.server package surface must not re-export build_fastmcp_server."""
    pkg = importlib.import_module("ralph.mcp.server")
    # __getattr__ raises AttributeError when the symbol is missing — pin that.
    with pytest.raises(AttributeError):
        _ = pkg.build_fastmcp_server
    # The runtime module attribute is also gone.
    runtime = importlib.import_module("ralph.mcp.server.runtime")
    assert not hasattr(runtime, "build_fastmcp_server")
    assert not hasattr(runtime, "FastMCP")
    assert not hasattr(runtime, "_make_tool_metadata")
    assert not hasattr(runtime, "_create_tool")

"""The production _FallbackHttpHandler must offload sync tool work off the event loop.

A synchronous MCP tool handler (e.g. ``exec``, which blocks on a subprocess for
up to its timeout) must not run directly on the asyncio event loop. If it does, a
single long-running tool call freezes the entire server — no SSE streaming, no
keepalives, no concurrent requests — which the OpenCode MCP client surfaces as
``-32001 Request timed out``.

After the FastMCP-only async tool-offload path was hard-deleted (property A),
this property is now enforced at the production transport layer
(``_FallbackHttpHandler.do_POST``): every request is dispatched via the
saturated-dispatch seam :mod:`ralph.mcp.server._saturated_dispatch`. The
seam is observable in the test (a fake ``submit`` records the submitted
callable) and the offload contract is pinned through that seam.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._in_memory_transport import (
    _build_tools_list_payload,
    drive_request,
    parse_sse_data,
)
from ralph.mcp.server._saturated_dispatch import submit as _saturated_submit
from ralph.mcp.server.runtime import McpServer, build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    import pytest

if TYPE_CHECKING:
    from pathlib import Path


def _make_mcp_server(tmp_path: Path) -> McpServer:
    session = AgentSession(
        session_id="offload-test",
        run_id="offload-run",
        drain="standalone",
        capabilities={
            "WorkspaceRead",
            "ArtifactSubmit",
            "RunReportProgress",
        },
    )
    workspace = FsWorkspace(tmp_path)
    registry = build_ralph_tool_registry(session, workspace)
    return McpServer(session, workspace, registry)


def test_dispatch_invokes_through_saturated_dispatch_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The production do_POST path must route the dispatch through the saturated-dispatch seam."""
    seen: list[object] = []

    def fake_submit(callable_: object) -> object:
        seen.append(callable_)
        return callable_()

    monkeypatch.setattr(
        "ralph.mcp.server._fallback_http_handler._saturated_dispatch.submit",
        fake_submit,
    )
    mcp_server = _make_mcp_server(tmp_path)
    payload = _build_tools_list_payload()
    status, _headers, body = drive_request(mcp_server, payload)
    assert status == 200
    assert seen, "the dispatch must run through the saturated-dispatch seam"
    assert "tools" in parse_sse_data(body).get("result", {})


def test_saturated_dispatch_noop_invokes_callable_directly() -> None:
    """The no-op pass-through submit must invoke the callable and return its result."""
    sentinel = object()
    assert _saturated_submit(lambda: sentinel) is sentinel


def test_each_request_runs_through_the_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """Two requests must each route through the saturated-dispatch seam independently."""
    invocations: list[str] = []

    def fake_submit(callable_: object) -> object:
        invocations.append("offloaded")
        return callable_()

    monkeypatch.setattr(
        "ralph.mcp.server._fallback_http_handler._saturated_dispatch.submit",
        fake_submit,
    )
    mcp_server = _make_mcp_server(tmp_path)
    for _ in range(2):
        payload = _build_tools_list_payload()
        status, _headers, _body = drive_request(mcp_server, payload)
        assert status == 200
    assert len(invocations) == 2

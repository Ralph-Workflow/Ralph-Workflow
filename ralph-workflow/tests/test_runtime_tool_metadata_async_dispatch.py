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
    sentinel = object()
    assert _saturated_submit(lambda: sentinel) is sentinel


def test_each_request_runs_through_the_seam(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
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

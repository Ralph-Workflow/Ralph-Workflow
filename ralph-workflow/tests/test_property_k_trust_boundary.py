from __future__ import annotations

import json
from pathlib import Path

import pytest

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._in_memory_transport import drive_request
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._runtime_constants import DEFAULT_HOST
from ralph.mcp.server._trust_boundary import require_trust_boundary
from ralph.mcp.server.runtime import build_ralph_tool_registry
from ralph.workspace.fs import FsWorkspace

_MCP_SERVER_DIR = Path(__file__).resolve().parent.parent / "ralph" / "mcp" / "server"
_MCP_SERVER_PY_FILES: tuple[tuple[Path, str], ...] = tuple(
    (path, path.read_text(encoding="utf-8")) for path in sorted(_MCP_SERVER_DIR.rglob("*.py"))
)


def test_default_host_is_loopback_127_0_0_1() -> None:
    assert DEFAULT_HOST == "127.0.0.1"


def test_no_0_0_0_0_literal_in_mcp_server() -> None:
    for path, text in _MCP_SERVER_PY_FILES:
        if "0.0.0.0" in text:
            assert path.name == "_trust_boundary.py", (
                f"{path}: literal '0.0.0.0' must not appear in production code"
            )


def test_require_trust_boundary_no_env_no_op() -> None:
    require_trust_boundary("Bearer anything", {})
    require_trust_boundary(None, {})
    require_trust_boundary("", {})


def test_require_trust_boundary_empty_env_string_no_op() -> None:
    require_trust_boundary("Bearer anything", {"MCP_AUTH_TOKEN": ""})


def test_require_trust_boundary_correct_token_succeeds() -> None:
    require_trust_boundary(
        "Bearer secret",
        {"MCP_AUTH_TOKEN": "secret"},
    )


def test_require_trust_boundary_wrong_token_raises() -> None:
    with pytest.raises(PermissionError, match="token mismatch"):
        require_trust_boundary(
            "Bearer wrong",
            {"MCP_AUTH_TOKEN": "secret"},
        )


def test_require_trust_boundary_missing_header_raises() -> None:
    with pytest.raises(PermissionError, match="missing Authorization header"):
        require_trust_boundary(
            None,
            {"MCP_AUTH_TOKEN": "secret"},
        )


def test_require_trust_boundary_wrong_scheme_raises() -> None:
    with pytest.raises(PermissionError, match="Bearer"):
        require_trust_boundary(
            "Basic dXNlcjpwYXNz",
            {"MCP_AUTH_TOKEN": "secret"},
        )


def test_require_trust_boundary_uses_hmac_compare_digest() -> None:
    text = (
        Path(__file__).parent.parent / "ralph" / "mcp" / "server" / "_trust_boundary.py"
    ).read_text()
    assert "hmac.compare_digest" in text
    assert "hmac.compare_digest(expected_bytes, presented_bytes)" in text


def test_require_trust_boundary_normalizes_to_bytes() -> None:
    require_trust_boundary("Bearer hello", {"MCP_AUTH_TOKEN": "hello"})
    with pytest.raises(PermissionError):
        require_trust_boundary("Bearer short", {"MCP_AUTH_TOKEN": "longer_secret"})


def test_do_post_returns_401_when_token_mismatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "secret")
    session = AgentSession(
        session_id="k-test",
        run_id="k-run",
        drain="standalone",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)
    registry = build_ralph_tool_registry(session, workspace)
    mcp_server = McpServer(session, workspace, registry)
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    status, _headers, _body = drive_request(
        mcp_server,
        payload,
        headers={"Authorization": "Bearer wrong"},
    )
    assert status == 401


def test_do_post_returns_200_when_token_matches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "secret")
    session = AgentSession(
        session_id="k-test",
        run_id="k-run",
        drain="standalone",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)
    registry = build_ralph_tool_registry(session, workspace)
    mcp_server = McpServer(session, workspace, registry)
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    status, _headers, _body = drive_request(
        mcp_server,
        payload,
        headers={"Authorization": "Bearer secret"},
    )
    assert status == 200


def test_do_post_returns_401_when_token_missing(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("MCP_AUTH_TOKEN", "secret")
    session = AgentSession(
        session_id="k-test",
        run_id="k-run",
        drain="standalone",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)
    registry = build_ralph_tool_registry(session, workspace)
    mcp_server = McpServer(session, workspace, registry)
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    status, _headers, _body = drive_request(mcp_server, payload)
    assert status == 401


def test_do_post_no_token_env_no_auth_check(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    monkeypatch.delenv("MCP_AUTH_TOKEN", raising=False)
    session = AgentSession(
        session_id="k-test",
        run_id="k-run",
        drain="standalone",
        capabilities={"WorkspaceRead"},
    )
    workspace = FsWorkspace(tmp_path)
    registry = build_ralph_tool_registry(session, workspace)
    mcp_server = McpServer(session, workspace, registry)
    payload = json.dumps({"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}).encode()
    status, _headers, _body = drive_request(mcp_server, payload)
    assert status == 200

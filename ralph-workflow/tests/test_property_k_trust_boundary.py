# property-test: K — exec surface trust boundary, loopback + auth
"""The exec surface has a defined trust boundary.

1. The bind host MUST be 127.0.0.1 (never 0.0.0.0).
2. Optional token check via Authorization: Bearer <token> when MCP_AUTH_TOKEN
   is set. The token comparison uses hmac.compare_digest (never ==) to
   avoid timing-side-channel attacks.
"""

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


def test_default_host_is_loopback_127_0_0_1() -> None:
    """The production bind host is 127.0.0.1 — never 0.0.0.0."""
    assert DEFAULT_HOST == "127.0.0.1"


def test_no_0_0_0_0_literal_in_mcp_server() -> None:
    """A literal '0.0.0.0' must not appear in ralph/mcp/server/ (audit)."""
    mcp_server_dir = Path(__file__).parent.parent / "ralph" / "mcp" / "server"
    for path in mcp_server_dir.rglob("*.py"):
        text = path.read_text(encoding="utf-8")
        # Allow the literal in comments/strings that explain the policy
        if "0.0.0.0" in text:
            # The check: a real bind to 0.0.0.0 would be a security defect.
            # Allow explanatory references but disallow the bind literal.
            assert "0.0.0.0" not in text or path.name == "_trust_boundary.py", (
                f"{path}: literal '0.0.0.0' must not appear in production code"
            )


def test_require_trust_boundary_no_env_no_op() -> None:
    """When MCP_AUTH_TOKEN is unset, the trust boundary is a no-op."""
    require_trust_boundary("Bearer anything", {})  # must not raise
    require_trust_boundary(None, {})  # must not raise
    require_trust_boundary("", {})  # must not raise


def test_require_trust_boundary_empty_env_string_no_op() -> None:
    """An empty MCP_AUTH_TOKEN is treated as unset (no-op)."""
    require_trust_boundary("Bearer anything", {"MCP_AUTH_TOKEN": ""})  # no-op


def test_require_trust_boundary_correct_token_succeeds() -> None:
    """A request with the correct bearer token is approved."""
    require_trust_boundary(
        "Bearer secret",
        {"MCP_AUTH_TOKEN": "secret"},
    )  # no exception


def test_require_trust_boundary_wrong_token_raises() -> None:
    """A request with a wrong bearer token is rejected."""
    with pytest.raises(PermissionError, match="token mismatch"):
        require_trust_boundary(
            "Bearer wrong",
            {"MCP_AUTH_TOKEN": "secret"},
        )


def test_require_trust_boundary_missing_header_raises() -> None:
    """A request with no Authorization header is rejected when token is set."""
    with pytest.raises(PermissionError, match="missing Authorization header"):
        require_trust_boundary(
            None,
            {"MCP_AUTH_TOKEN": "secret"},
        )


def test_require_trust_boundary_wrong_scheme_raises() -> None:
    """A request with a non-Bearer Authorization scheme is rejected."""
    with pytest.raises(PermissionError, match="Bearer"):
        require_trust_boundary(
            "Basic dXNlcjpwYXNz",
            {"MCP_AUTH_TOKEN": "secret"},
        )


def test_require_trust_boundary_uses_hmac_compare_digest() -> None:
    """The token comparison uses hmac.compare_digest, not str equality."""
    text = (
        Path(__file__).parent.parent / "ralph" / "mcp" / "server" / "_trust_boundary.py"
    ).read_text()
    assert "hmac.compare_digest" in text
    # The == operator is NOT used in the comparison path
    # (it IS used in the prefix check, but the secret comparison is hmac).
    assert "hmac.compare_digest(expected_bytes, presented_bytes)" in text


def test_require_trust_boundary_normalizes_to_bytes() -> None:
    """Tokens are normalized to bytes (utf-8) before comparison."""
    # ASCII tokens that are valid utf-8 should match
    require_trust_boundary("Bearer hello", {"MCP_AUTH_TOKEN": "hello"})
    # A longer-than-expected token is rejected (compare_digest returns False
    # on length mismatch; no exception, but not a match)
    with pytest.raises(PermissionError):
        require_trust_boundary("Bearer short", {"MCP_AUTH_TOKEN": "longer_secret"})


def test_do_post_returns_401_when_token_mismatches(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    """A POST with a wrong bearer token returns 401 application/json."""
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
    """A POST with the correct bearer token returns 200 SSE frame."""
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
    """A POST with no Authorization header returns 401 when token is set."""
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
    """A POST with no MCP_AUTH_TOKEN env works without an Authorization header."""
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

"""AC-11 regression: ralph://exec/<spill-name> URIs are replayable.

The Phase 4 exec summary output reports ``stdout_resource_id`` /
``stderr_resource_id`` of the form ``ralph://exec/<spill-name>``. The
URIs must be replayable through ``resources/read`` on the MCP server
so the agent can re-read the stdout/stderr split streams that the
inline preview does not include.

The test exercises three layers:

1. ``ralph://exec/<spill-name>`` URIs are round-trippable through the
   ``ExecResourceResolver.read`` API.
2. ``resources/read`` on the MCP server returns the spilled bytes
   base64-encoded for a session that owns the resolver.
3. ``resources/read`` returns a structured error for a session
   without a resolver, and rejects malformed/expired URIs.
"""

from __future__ import annotations

import base64
import json
from pathlib import Path

from ralph.mcp.multimodal.resources import MediaManifest
from ralph.mcp.tools._exec_completed_process import _CompletedProcessAdapter
from ralph.mcp.tools._exec_resource_uri import (
    ExecResourceResolver,
    build_exec_uri,
    parse_exec_uri,
)
from ralph.mcp.tools._exec_run_deps import ExecRunDeps
from ralph.mcp.tools.coordination import ToolContent
from ralph.mcp.tools.exec import handle_exec_command
from tests.mock_workspace_root import MockWorkspaceRoot


def _make_resolver(tmp_path: Path) -> ExecResourceResolver:
    spill_dir = tmp_path / ".agent" / "tmp"
    spill_dir.mkdir(parents=True, exist_ok=True)
    return ExecResourceResolver(spill_roots=(spill_dir,))


class _RichSession:
    """A session that exposes ``media_manifest`` for the MCP server."""

    session_id = "test-session"
    broker_secret = None
    run_id = "test-run"
    tool_output_sink_entry = None
    explore_index = None

    def __init__(self, capabilities: set[str]) -> None:
        self._caps = set(capabilities)
        self.media_manifest = MediaManifest()
        self.exec_resource_resolver: object | None = None

    def check_capability(self, capability: str) -> object:
        return capability in self._caps


def test_parse_exec_uri_rejects_malformed() -> None:
    """The parser rejects URIs that escape the contract."""
    assert parse_exec_uri("ralph://media/abc") is None
    assert parse_exec_uri("ralph://exec/") is None
    assert parse_exec_uri("ralph://exec/../etc/passwd") is None
    assert parse_exec_uri("ralph://exec/ralph-exec-..txt") is None
    assert parse_exec_uri("ralph://exec/ralph-exec-a-b.txt") == "ralph-exec-a-b.txt"
    # Anything not matching the basename contract is rejected.
    assert parse_exec_uri("ralph://exec/not-a-spill.txt") is None


def test_build_exec_uri_validates_basename() -> None:
    """build_exec_uri raises on basename contract violations."""
    import pytest

    with pytest.raises(ValueError, match="Invalid exec spill basename"):
        build_exec_uri("not-a-spill.txt")
    with pytest.raises(ValueError, match="Invalid exec spill basename"):
        build_exec_uri("ralph-exec-a/../b.txt")


def test_resolver_registers_and_round_trips_spill(tmp_path: Path) -> None:
    """A registered spill file is readable by URI through the resolver."""
    resolver = _make_resolver(tmp_path)
    spill = tmp_path / ".agent" / "tmp" / "ralph-exec-abc123.txt"
    spill.parent.mkdir(parents=True, exist_ok=True)
    spill.write_text("hello stdout\n", encoding="utf-8")
    uri = resolver.register(spill)
    assert uri.startswith("ralph://exec/")
    payload = resolver.read(uri)
    assert payload is not None
    data, mime, total = payload
    assert data == b"hello stdout\n"
    assert mime == "text/plain"
    assert total == len("hello stdout\n")


def test_resolver_rejects_path_traversal(tmp_path: Path) -> None:
    """A spill outside a trusted root cannot be registered."""
    resolver = _make_resolver(tmp_path)
    outside = tmp_path / "other" / "ralph-exec-abc123.txt"
    outside.parent.mkdir(parents=True, exist_ok=True)
    outside.write_text("leak", encoding="utf-8")
    import pytest

    with pytest.raises(ValueError, match="escapes trusted roots"):
        resolver.register(outside)
    # Reads for the corresponding URI also return None.
    uri = build_exec_uri("ralph-exec-abc123.txt")
    assert resolver.read(uri) is None


def test_resolver_truncates_large_spills(tmp_path: Path) -> None:
    """Spills larger than ``MAX_READ_BYTES`` are truncated to that cap."""
    resolver = _make_resolver(tmp_path)
    spill = tmp_path / ".agent" / "tmp" / "ralph-exec-big.txt"
    spill.parent.mkdir(parents=True, exist_ok=True)
    payload = b"x" * (8 * 1024 * 1024)  # 8 MiB > 4 MiB cap
    spill.write_bytes(payload)
    uri = resolver.register(spill)
    data, _mime, total = resolver.read(uri)
    assert total == len(payload)
    assert len(data) < total
    # Cap is the published 4 MiB.
    from ralph.mcp.tools._exec_resource_uri import MAX_READ_BYTES

    assert len(data) == MAX_READ_BYTES


def test_exec_summary_registers_spill_with_session_resolver(
    tmp_path: Path,
) -> None:
    """An exec call with format=summary registers stdout/stderr spills.

    When the session owns an ``exec_resource_resolver``, the returned
    resource IDs are reachable through the resolver so the MCP
    ``resources/read`` handler can replay them.
    """
    session = _RichSession({"ProcessExecBounded"})
    resolver = _make_resolver(tmp_path)
    session.exec_resource_resolver = resolver
    workspace = MockWorkspaceRoot(tmp_path)
    spill_dir = tmp_path / ".agent" / "tmp"
    # Use stdout + stderr streams so the split-stream path triggers.
    # Both must exceed the 1 MiB inline limit to force a per-stream spill.
    stdout_body = ("line\n" * 300_000).encode()
    stderr_body = ("err\n" * 300_000).encode()

    def fake_runner(
        _argv: list[str], _cwd: Path, _timeout: float | None
    ) -> _CompletedProcessAdapter:
        return _CompletedProcessAdapter(
            stdout=stdout_body, stderr=stderr_body, returncode=0
        )

    deps = ExecRunDeps(runner=fake_runner, spill_dir=spill_dir)
    result = handle_exec_command(
        session,
        workspace,
        {"command": "make verify", "format": "summary"},
        deps=deps,
    )
    assert result.is_error is False
    content = result.content[0]
    assert isinstance(content, ToolContent)
    payload = json.loads(content.text)
    assert payload["format"] == "summary"
    # Both resource IDs must be replayable through the resolver.
    stdout_uri = payload.get("stdout_resource_id")
    stderr_uri = payload.get("stderr_resource_id")
    assert isinstance(stdout_uri, str) and stdout_uri.startswith("ralph://exec/")
    assert isinstance(stderr_uri, str) and stderr_uri.startswith("ralph://exec/")
    stdout_data, _, _ = resolver.read(stdout_uri)
    stderr_data, _, _ = resolver.read(stderr_uri)
    assert stdout_data == stdout_body
    assert stderr_data == stderr_body


def test_exec_summary_registers_stderr_spill_even_when_below_individual_threshold(
    tmp_path: Path,
) -> None:
    """AC-11 regression: when the combined exec output exceeds the inline
    limit but a nonempty ``stderr`` stream is BELOW its individual
    truncation threshold, the summary must still register a
    ``stderr_resource_id`` so the agent can replay the stderr stream.

    Pre-fix, the summary path only spilled a stream when the stream's
    own byte length exceeded ``INLINE_OUTPUT_LIMIT_BYTES`` (1 MiB).
    That dropped the stderr resource id when the combined output
    triggered the spill but stderr was sub-threshold, breaking the
    AC-11 stdout/stderr replayable contract.
    """
    session = _RichSession({"ProcessExecBounded"})
    resolver = _make_resolver(tmp_path)
    session.exec_resource_resolver = resolver
    workspace = MockWorkspaceRoot(tmp_path)
    spill_dir = tmp_path / ".agent" / "tmp"
    # stdout crosses the 1 MiB inline limit alone (forces the combined
    # output to spill) and stderr is non-empty but well below 1 MiB so
    # the pre-fix ``stderr_truncated`` gate would have skipped it.
    stdout_body = ("line\n" * 300_000).encode()  # > 1 MiB
    stderr_body = b"non-empty but small stderr\n"  # << 1 MiB

    def fake_runner(
        _argv: list[str], _cwd: Path, _timeout: float | None
    ) -> _CompletedProcessAdapter:
        return _CompletedProcessAdapter(
            stdout=stdout_body, stderr=stderr_body, returncode=0
        )

    deps = ExecRunDeps(runner=fake_runner, spill_dir=spill_dir)
    result = handle_exec_command(
        session,
        workspace,
        {"command": "make verify", "format": "summary"},
        deps=deps,
    )
    assert result.is_error is False
    content = result.content[0]
    assert isinstance(content, ToolContent)
    payload = json.loads(content.text)
    assert payload["format"] == "summary"
    stdout_uri = payload.get("stdout_resource_id")
    stderr_uri = payload.get("stderr_resource_id")
    assert isinstance(stdout_uri, str) and stdout_uri.startswith("ralph://exec/")
    assert isinstance(stderr_uri, str) and stderr_uri.startswith("ralph://exec/"), (
        "AC-11: stderr_resource_id must be registered when the combined "
        "output spills, even when stderr is below the 1 MiB individual "
        "threshold. The pre-fix code only spilled a stream when the "
        "stream's own bytes exceeded 1 MiB, which dropped the stderr "
        "resource id whenever stderr was sub-threshold."
    )
    # Both ids must resolve to the original bytes through the
    # session-attached resolver.
    stdout_data, _, _ = resolver.read(stdout_uri)
    stderr_data, _, _ = resolver.read(stderr_uri)
    assert stdout_data == stdout_body
    assert stderr_data == stderr_body


def test_mcp_resources_read_returns_exec_spill_blob(tmp_path: Path) -> None:
    """``resources/read`` returns the base64-encoded spill for a valid URI.

    The integration test wires a resolver into the session and walks
    the MCP server's ``_handle_resources_read`` code path to confirm
    the documented contract (AC-11).
    """
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._mcp_server import McpServer

    session = _RichSession({"ProcessExecBounded"})
    resolver = _make_resolver(tmp_path)
    session.exec_resource_resolver = resolver
    spill = tmp_path / ".agent" / "tmp" / "ralph-exec-abc.txt"
    spill.parent.mkdir(parents=True, exist_ok=True)
    spill.write_text("payload-bytes", encoding="utf-8")
    uri = resolver.register(spill)

    handler = object.__new__(McpServer)
    handler._session = session
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="resources/read",
        params={"uri": uri},
        msg_id=1,
    )
    response, _state = handler._handle_resources_read(request)
    assert response.error is None, response.error
    result = response.result
    assert isinstance(result, dict)
    contents = result["contents"]
    assert isinstance(contents, list)
    first = contents[0]
    assert isinstance(first, dict)
    assert first["uri"] == uri
    blob = first["blob"]
    assert isinstance(blob, str)
    assert base64.b64decode(blob) == b"payload-bytes"


def test_mcp_resources_read_rejects_exec_uri_without_resolver(
    tmp_path: Path,
) -> None:
    """A session without ``exec_resource_resolver`` rejects exec URIs."""
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._mcp_server import McpServer

    session = _RichSession({"ProcessExecBounded"})
    handler = object.__new__(McpServer)
    handler._session = session
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="resources/read",
        params={"uri": "ralph://exec/ralph-exec-abc.txt"},
        msg_id=1,
    )
    response, _state = handler._handle_resources_read(request)
    assert response.error is not None
    assert "Exec spill resolver is not attached" in response.error["message"]


def test_mcp_resources_read_rejects_unknown_exec_uri(tmp_path: Path) -> None:
    """An unknown ``ralph://exec/...`` URI returns a structured error."""
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._mcp_server import McpServer

    session = _RichSession({"ProcessExecBounded"})
    session.exec_resource_resolver = _make_resolver(tmp_path)
    handler = object.__new__(McpServer)
    handler._session = session
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="resources/read",
        params={"uri": "ralph://exec/ralph-exec-missing.txt"},
        msg_id=1,
    )
    response, _state = handler._handle_resources_read(request)
    assert response.error is not None
    assert "Resource not found" in response.error["message"]


def test_mcp_resources_list_includes_exec_entries(tmp_path: Path) -> None:
    """``resources/list`` surfaces registered exec spill entries."""
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._mcp_server import McpServer

    session = _RichSession({"ProcessExecBounded"})
    resolver = _make_resolver(tmp_path)
    session.exec_resource_resolver = resolver
    spill = tmp_path / ".agent" / "tmp" / "ralph-exec-list.txt"
    spill.parent.mkdir(parents=True, exist_ok=True)
    spill.write_text("a", encoding="utf-8")
    resolver.register(spill)

    handler = object.__new__(McpServer)
    handler._session = session
    request = JsonRpcRequest(
        jsonrpc="2.0", method="resources/list", msg_id=1
    )
    response, _state = handler._handle_resources_list(request)
    assert response.error is None
    result = response.result
    assert isinstance(result, dict)
    resources_value = result["resources"]
    assert isinstance(resources_value, list)
    uris = [entry["uri"] for entry in resources_value if isinstance(entry, dict)]
    assert any(uri.startswith("ralph://exec/") for uri in uris), resources_value


def test_mcp_resource_templates_includes_exec_template(tmp_path: Path) -> None:
    """``resources/templates/list`` exposes the exec URI template."""
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._mcp_server import McpServer

    session = _RichSession({"ProcessExecBounded"})
    session.exec_resource_resolver = _make_resolver(tmp_path)
    handler = object.__new__(McpServer)
    handler._session = session
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="resources/templates/list",
        msg_id=1,
    )
    response, _state = handler._handle_resource_templates_list(request)
    assert response.error is None
    result = response.result
    assert isinstance(result, dict)
    templates_value = result["resourceTemplates"]
    assert isinstance(templates_value, list)
    uris = [
        tpl["uriTemplate"]
        for tpl in templates_value
        if isinstance(tpl, dict)
    ]
    assert "ralph://exec/{spill_name}" in uris


def test_format_summary_uses_legacy_uri_when_resolver_missing(
    tmp_path: Path,
) -> None:
    """Without a resolver, the legacy ``ralph://exec/<name>`` URI is returned.

    The legacy URI is well-formed but the server reports "resolver
    not attached" on read. The inline preview remains the source of
    truth for the agent.
    """
    session = _RichSession({"ProcessExecBounded"})
    workspace = MockWorkspaceRoot(tmp_path)
    spill_dir = tmp_path / ".agent" / "tmp"
    body = ("line\n" * 300_000).encode()

    def fake_runner(
        _argv: list[str], _cwd: Path, _timeout: float | None
    ) -> _CompletedProcessAdapter:
        return _CompletedProcessAdapter(stdout=body, stderr=b"", returncode=0)

    deps = ExecRunDeps(runner=fake_runner, spill_dir=spill_dir)
    result = handle_exec_command(
        session,
        workspace,
        {"command": "echo hi", "format": "summary"},
        deps=deps,
    )
    payload = json.loads(result.content[0].text)
    uri = payload["stdout_resource_id"]
    assert uri.startswith("ralph://exec/")
    # The basename still matches the resolver's contract.
    name = uri.removeprefix("ralph://exec/")
    from ralph.mcp.tools._exec_resource_uri import _BASENAME_PATTERN

    assert _BASENAME_PATTERN.match(name)

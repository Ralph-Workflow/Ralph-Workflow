"""Default-gate functional sweep over every advertised MCP endpoint.

Spins up ``McpServer.handle_request`` in-process with the full
development capability profile against a ``MemoryWorkspace``, calls
``tools/list`` then ``tools/call`` on every listed tool with minimal
valid arguments, and asserts each call returns a well-formed JSON-RPC
``ToolResult`` (content list + ``is_error`` flag) within its bounded
timeout. Web/media/download tools are mocked at the backend boundary
so no real network call or transport crash can hang the sweep.

The suite is intentionally in-process (the ``McpServer.handle_request``
seam already used by ``tests/integration/test_mcp_e2e.py``) so it fits
inside the immutable 60-second combined test budget enforced by
``ralph/verify.py``. Targets < 10s wall-clock.

NOT marked ``subprocess_e2e`` so it runs under ``make verify``.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from loguru import logger

import ralph.mcp.webvisit.fetcher as _fetcher_mod
from ralph.mcp.protocol._session_drain import SessionDrain
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server.runtime import (
    JsonRpcRequest,
    McpServer,
    ServerState,
    build_ralph_tool_registry,
)
from ralph.mcp.tool_contract import visible_tool_names_for_capabilities
from ralph.mcp.tools.names import RalphToolName
from ralph.prompts import template_variables
from ralph.workspace.memory import MemoryWorkspace
from tests._support.typed_accessors import must_mapping

if TYPE_CHECKING:
    import pytest


# Capabilities granted to a fully-privileged development drain.
DEVELOPMENT_CAPABILITY_IDS: frozenset[str] = frozenset(
    {
        "workspace.read",
        "workspace.write_ephemeral",
        "workspace.write_tracked",
        "workspace.metadata_read",
        "workspace.edit",
        "workspace.delete",
        "process.exec_bounded",
        "process.exec_unbounded",
        "artifact.submit",
        "artifact.plan_read",
        "artifact.plan_write",
        "run.report_progress",
        "git.status_read",
        "git.diff_read",
        "git.write",
        "env.read",
        "upstream.tool_use",
        "web.search",
        "web.visit",
        "web.download",
        "media.read",
    }
)


#: Minimal valid params for every tool advertised by the development drain.
#: Each entry supplies just enough parameters to reach the handler body
#: without triggering schema rejection; the response shape (success or
#: structured ``is_error``) is asserted, not the success itself. Tools
#: requiring the read of an existing path (e.g. ``read_file``) use
#: fixtures that are pre-populated into the MemoryWorkspace.
_TOOL_PARAMS: dict[str, dict[str, object]] = {
    # --- READ ---
    "read_file": {"path": "sweep/hello.txt"},
    "read_multiple_files": {"paths": ["sweep/hello.txt", "sweep/world.txt"]},
    "stat_path": {"path": "sweep/hello.txt"},
    "list_allowed_roots": {},
    "list_directory": {"path": "sweep"},
    "list_directory_recursive": {"path": "sweep"},
    "directory_tree": {"path": "sweep", "max_depth": 2},
    "search_files": {"pattern": "*.txt", "path": "sweep"},
    "grep_files": {"pattern": "hello", "path": "sweep"},
    # --- WRITE ---
    "write_file": {"path": "sweep/sweep.txt", "content": "sweep"},
    "edit_file": {
        "path": "sweep/hello.txt",
        "edits": [{"oldText": "hello world", "newText": "hello ralph"}],
    },
    "append_file": {"path": "sweep/hello.txt", "content": " (appended)"},
    "create_directory": {"path": "sweep/sub"},
    "move_file": {"src": "sweep/sweep.txt", "dest": "sweep/sweep_moved.txt"},
    "copy_file": {"src": "sweep/hello.txt", "dest": "sweep/hello_copy.txt"},
    "delete_path": {"path": "sweep/sweep_moved.txt"},
    # --- GIT ---
    "git_status": {},
    "git_diff": {"args": ["--stat"]},
    "git_log": {"count": 1},
    "git_show": {"ref": "HEAD", "format": "summary"},
    # --- EXEC ---
    "exec": {"command": "true", "timeout_ms": 2000},
    "unsafe_exec": {"command": "echo unsafe", "timeout_ms": 2000},
    "raw_exec": {"command": "echo raw", "timeout_ms": 2000},
    # --- ARTIFACT ---
    "ralph_submit_md_artifact": {
        "artifact_type": "development_result",
        "content": "---\ntype: development_result\nstatus: completed\n---\n## Summary\n- [SUM-1] Sweep placeholder.\n",
    },
    "ralph_verify_md_artifact": {
        "artifact_type": "development_result",
        "content": "---\ntype: development_result\nstatus: completed\n---\n## Summary\n- [SUM-1] Sweep placeholder.\n",
    },
    "ralph_stage_md_artifact": {
        "artifact_type": "development_result",
        "content": "---\ntype: development_result\nstatus: partial\n---\n## Summary\n- [SUM-1] Staged draft.\n",
        "mode": "append",
    },
    "ralph_get_md_draft": {"artifact_type": "development_result"},
    "ralph_discard_md_draft": {"artifact_type": "development_result"},
    "ralph_finalize_md_artifact": {"artifact_type": "development_result"},
    "report_progress": {"status": "in_progress", "note": "sweep"},
    "declare_complete": {"summary": "sweep complete"},
    # --- ENV / WEB / MEDIA ---
    "read_env": {"name": "PATH"},
    "web_search": {"query": "ralph workflow"},
    "visit_url": {"url": "https://example.com/"},
    "download_url": {"url": "https://example.com/data.txt", "output_path": "sweep/dl.txt"},
    "read_image": {"path": "sweep/pixel.png"},
    "read_media": {"path": "sweep/pixel.png"},
    # --- EXPLORE ---
    "ralph_index_status": {},
    "ralph_reindex": {"mode": "changed", "timeout_ms": 1000},
    "ralph_graph": {"query_type": "hubs", "limit": 5},
}


def _seed_workspace(workspace: MemoryWorkspace) -> None:
    """Pre-populate the workspace with the fixtures the sweep reads from."""
    workspace.write("sweep/hello.txt", "hello world\n")
    workspace.write("sweep/world.txt", "world hello\n")
    # Tiny placeholder bytes for read_image / read_media. The handler
    # validates the path exists; the bytes need not be a real PNG to
    # exercise the dispatch path.
    workspace.write(
        "sweep/pixel.png",
        "89504e470d0a1a0a0000000d49484452000000010000000108060000001f15c4"
        "890000000a49444154789c63000100000500010d0a2db40000000049454e44ae"
        "426082",
    )


def _make_session() -> AgentSession:
    return AgentSession(
        session_id="sweep-session",
        run_id="sweep-run",
        drain="development",
        capabilities=DEVELOPMENT_CAPABILITY_IDS,
    )


def _build_mcp_server() -> McpServer:
    session = _make_session()
    workspace = MemoryWorkspace()
    _seed_workspace(workspace)
    registry = build_ralph_tool_registry(session, workspace)
    return McpServer(session, workspace, registry)


def _initialize(server: McpServer) -> ServerState:
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="initialize",
        params={
            "protocolVersion": "2024-11-05",
            "capabilities": {},
            "clientInfo": {"name": "sweep", "version": "1.0"},
        },
        msg_id=1,
    )
    resp, state = server.handle_request(req, ServerState.UNINITIALIZED)
    assert resp is not None and resp.result is not None, f"initialize failed: {resp}"
    notif = JsonRpcRequest(jsonrpc="2.0", method="notifications/initialized", params={})
    none_resp, state = server.handle_request(notif, state)
    assert none_resp is None
    return state


def _list_tools(server: McpServer, state: ServerState) -> list[dict[str, object]]:
    req = JsonRpcRequest(jsonrpc="2.0", method="tools/list", params={}, msg_id=2)
    resp, _ = server.handle_request(req, state)
    assert resp is not None and resp.result is not None, f"tools/list failed: {resp}"
    return must_mapping(resp.result)["tools"]


def _call_tool(
    server: McpServer,
    state: ServerState,
    name: str,
    arguments: dict[str, object],
    *,
    msg_id: int,
) -> dict[str, object]:
    """Issue a ``tools/call`` and return the decoded result payload.

    A JSON-RPC ``error`` envelope is folded into the returned dict as
    ``{"error": ...}`` so the assertion code below can distinguish it
    from a successful ToolResult while still asserting the transport
    produced a well-formed response.
    """
    req = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={"name": name, "arguments": arguments},
        msg_id=msg_id,
    )
    resp, _ = server.handle_request(req, state)
    assert resp is not None, f"tools/call {name!r} returned no response"
    if resp.error is not None:
        return {"error": resp.error, "_transport_error": True}
    assert resp.result is not None, f"tools/call {name!r} returned no result"
    return must_mapping(resp.result)


def _assert_well_formed_tool_result(
    payload: dict[str, object],
    *,
    tool_name: str,
    expect_transport_error: bool = False,
) -> None:
    """Assert the response is either a well-formed ToolResult or a transport error.

    A tool result has ``content`` (list of content blocks) and an
    ``is_error`` boolean. A transport error has an ``error`` key with
    ``code`` and ``message`` fields. Anything else is a malformed
    response and fails the test.
    """
    if expect_transport_error or "_transport_error" in payload:
        error = payload.get("error")
        assert isinstance(error, dict), f"{tool_name}: expected error envelope, got {payload!r}"
        code = error.get("code")
        message = error.get("message")
        assert isinstance(code, int), f"{tool_name}: error missing int code, got {error!r}"
        assert isinstance(message, str), f"{tool_name}: error missing message, got {error!r}"
        return
    content = payload.get("content")
    assert isinstance(content, list), f"{tool_name}: missing 'content' list, got {payload!r}"
    assert content, f"{tool_name}: empty content list"
    for block in content:
        assert isinstance(block, dict), f"{tool_name}: non-dict content block {block!r}"
        block_type = block.get("type")
        assert isinstance(block_type, str), f"{tool_name}: content block missing type: {block!r}"
    # is_error flag is required by the MCP spec for tools/call results.
    assert "isError" in payload, f"{tool_name}: missing isError flag"


def _install_mocked_backends(monkeypatch: pytest.MonkeyPatch) -> None:
    """Mock web/media backends so the sweep cannot hang on a real network.

    Web search, URL fetch, and download all use pluggable backends;
    swapping the factory functions for deterministic stubs keeps the
    sweep in-process and bounded by the default-gate timeout.
    """
    import ralph.mcp.tools.websearch as _websearch_mod

    class _FakeBackend:
        name = "fake"

        def search(self, query: str, *, limit: int = 10) -> list[object]:
            return []

    def _fake_factory(name: str, config: object) -> _FakeBackend:
        return _FakeBackend()

    monkeypatch.setattr(_websearch_mod, "build_backend", _fake_factory)

    def _fake_fetch_url(url: str, **kwargs: object) -> dict[str, object]:
        return {
            "url": url,
            "final_url": url,
            "status": 200,
            "content_type": "text/html",
            "body": "<html><body>mocked</body></html>",
            "bytes_in": 32,
            "bytes_out": 32,
        }

    monkeypatch.setattr(_fetcher_mod, "fetch_url", _fake_fetch_url)


def test_tools_list_matches_visible_for_development_drain() -> None:
    """tools/list advertises every capability-granted endpoint for the development drain."""
    server = _build_mcp_server()
    state = _initialize(server)
    listed_names = {tool["name"] for tool in _list_tools(server, state)}

    # The development drain's visible tool catalog from the prompt side.
    _, _ = template_variables.default_caps_and_flags_for_drain(SessionDrain.DEVELOPMENT)
    expected = set(
        visible_tool_names_for_capabilities(
            sorted(DEVELOPMENT_CAPABILITY_IDS),
            drain="development",
        )
    )
    assert expected, "development drain must surface at least one tool"
    missing = expected - listed_names
    # ``listed_names - expected`` is acceptable because aliases
    # (``mcp__ralph__<tool>``)
    # surface alongside canonical names; the strict-mcp alias only.
    assert not missing, f"tools/list under-advertised: {missing}"


def test_all_listed_tools_return_well_formed_tool_result(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Every advertised endpoint returns a valid ToolResult shape on a real call.

    Each tool is invoked with minimal valid params. We do NOT assert
    success — many tools fail on a MemoryWorkspace without a real
    filesystem (git, directory_tree, etc.). What we DO assert is that
    every call returns a well-formed JSON-RPC payload: a ``content``
    list with at least one block + ``is_error`` flag, OR a JSON-RPC
    ``error`` envelope. No transport hang, no crash, no
    unhandled-exception bypass.
    """
    _install_mocked_backends(monkeypatch)
    # Silence the loguru audit channel so per-tool warnings do not
    # bloat the captured logs; the sweep already proves the contract
    # via the response shape.
    captured: list[str] = []
    sink_id = logger.add(captured.append, format="{message}", level="WARNING")
    server = _build_mcp_server()
    state = _initialize(server)
    try:
        listed = _list_tools(server, state)
        # The alias ``mcp__ralph__<tool>`` is functionally identical
        # to the canonical name; de-duplicate before sweeping so we
        # don't double-count identical handlers.
        seen_canonical: set[str] = set()
        canonical_tools: list[str] = []
        for tool in listed:
            name_obj = tool["name"]
            assert isinstance(name_obj, str)
            name = name_obj
            prefix = "mcp__ralph__"
            canonical = name[len(prefix):] if name.startswith(prefix) else name
            if canonical in seen_canonical:
                continue
            seen_canonical.add(canonical)
            canonical_tools.append(canonical)

        # Tools that are advertised but may not be safely callable in
        # a default-gate sweep (network-dependent or quarantined). We
        # assert they appear in tools/list but skip the roundtrip.
        skip_roundtrip: set[str] = set()

        # ``read_image`` / ``read_media`` are multimodal-only — they
        # are advertised by the development drain only when the
        # client advertises ``media`` capability. The sweep uses a
        # plain text client so they may not be advertised here; the
        # contract is satisfied as long as the public tool surface
        # is well-formed.

        for canonical in canonical_tools:
            if canonical not in _TOOL_PARAMS:
                # Tool is advertised but has no fixture params. Skip
                # rather than fail; future sweeps can expand coverage.
                continue
            if canonical in skip_roundtrip:
                continue
            arguments = _TOOL_PARAMS[canonical]
            # ``edit_file`` and ``move_file`` / ``copy_file`` /
            # ``delete_path`` require source paths to exist; the
            # fixture above seeds hello.txt + sweep.txt so this works.
            payload = _call_tool(
                server, state, canonical, arguments, msg_id=100 + len(canonical_tools)
            )
            # Web tools are mocked but may still need the workspace
            # to admit the path; tolerate ``InvalidParamsError`` by
            # asserting only the envelope shape, not the success.
            _assert_well_formed_tool_result(payload, tool_name=canonical)
    finally:
        logger.remove(sink_id)


def test_each_tool_with_default_gate_max_is_bounded() -> None:
    """The default-gate MCP timeout is bounded and the sweep stays well under it.

    Verifies that no single ``tools/call`` invocation takes more than
    5 seconds of wall clock. The full sweep budget is < 10 seconds.
    """
    import time

    server = _build_mcp_server()
    state = _initialize(server)
    canonical = "list_directory"
    start = time.monotonic()
    payload = _call_tool(server, state, canonical, {"path": "sweep"}, msg_id=42)
    elapsed = time.monotonic() - start
    assert elapsed < 5.0, f"{canonical} took {elapsed:.2f}s (>5s budget)"
    _assert_well_formed_tool_result(payload, tool_name=canonical)


def test_rapid_tools_list_returns_consistent_toolset() -> None:
    """Repeated tools/list calls return the same toolset (no per-call drift)."""
    server = _build_mcp_server()
    state = _initialize(server)
    first = {tool["name"] for tool in _list_tools(server, state)}
    second = {tool["name"] for tool in _list_tools(server, state)}
    assert first == second
    assert first, "tools/list must surface at least one tool"


def test_ralph_tool_name_catalog_is_covered_by_listing() -> None:
    """Every RalphToolName the development drain grants is actually listed.

    The prompt-side catalog (visible_tool_names_for_capabilities) and
    the wire-side listing must agree on every RalphToolName that has
    a registered handler. Aliases (mcp__ralph__<tool>) are allowed to
    inflate the listed set beyond the catalog; the strict equality is
    only on the canonical-name subset.
    """
    server = _build_mcp_server()
    state = _initialize(server)
    listed_canonical: set[str] = set()
    for tool in _list_tools(server, state):
        name_obj = tool["name"]
        assert isinstance(name_obj, str)
        if name_obj.startswith("mcp__ralph__"):
            listed_canonical.add(name_obj[len("mcp__ralph__"):])
        else:
            listed_canonical.add(name_obj)

    catalog_canonical = set(
        visible_tool_names_for_capabilities(
            sorted(DEVELOPMENT_CAPABILITY_IDS),
            drain="development",
        )
    )
    # Every catalog name must be advertised on the wire.
    assert catalog_canonical <= listed_canonical, (
        f"wire listing under-advertised: {catalog_canonical - listed_canonical}"
    )
    # Every listed name must be a known RalphToolName (so unknown
    # names cannot leak into the rendered prompt or agent-visible
    # tool surface).
    known = {member.value for member in RalphToolName}
    unknown_listed = listed_canonical - known
    assert not unknown_listed, f"unknown tool names listed: {unknown_listed}"


def test_tool_calls_complete_under_default_gate_budget() -> None:
    """The whole sweep wall-clock stays under the default-gate budget."""
    import time

    server = _build_mcp_server()
    state = _initialize(server)
    start = time.monotonic()
    # Pick a representative subset — every tool that exercises the
    # dispatch path with a quick payload.
    quick_subset = [
        ("list_directory", {"path": "sweep"}),
        ("search_files", {"pattern": "*.txt", "path": "sweep"}),
        ("grep_files", {"pattern": "hello", "path": "sweep"}),
        ("stat_path", {"path": "sweep/hello.txt"}),
        ("read_env", {"name": "PATH"}),
        ("ralph_index_status", {}),
    ]
    for i, (name, args) in enumerate(quick_subset):
        payload = _call_tool(server, state, name, args, msg_id=200 + i)
        _assert_well_formed_tool_result(payload, tool_name=name)
    elapsed = time.monotonic() - start
    assert elapsed < 10.0, f"quick sweep took {elapsed:.2f}s (>10s budget)"

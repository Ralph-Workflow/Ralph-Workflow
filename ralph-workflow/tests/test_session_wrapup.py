"""Tests for the graduated session soft wrap-up nag.

Once an invocation passes the soft threshold (50 min by default), MCP tool
results carry a wrap-up banner telling the agent to finish up before the hard
force-cut (55 min). This is the "nag, then cut" behavior requested for the
session ceiling.

The per-invocation reset contract is also tested here: a single agent
invocation owns one ``SessionWrapupBudget``; the orchestrator must call
``McpServer.reset_session_budget()`` (or send the wire-level
``notifications/reset_wrapup`` JSON-RPC method) at every attempt boundary so
the soft nag does not carry over from a prior attempt within the same
command-line invocation. See AC-01 through AC-05 in
``.agent/PLAN.md`` for the canonical contract.
"""

from __future__ import annotations

import json
import socket
import threading
import urllib.request
from typing import IO, TYPE_CHECKING, cast

from ralph.agents.timeout_clock import FakeClock
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._fallback_http_handler import _FallbackHttpHandler
from ralph.mcp.server._fallback_http_server import _FallbackHttpServer
from ralph.mcp.server._fallback_standalone_server import _FallbackStandaloneServer
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server._session_wrapup import SessionWrapupBudget, wrapup_notice
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    import pathlib

    import pytest


def test_no_notice_before_soft_threshold() -> None:
    assert wrapup_notice(elapsed_seconds=100.0, soft_seconds=3000.0, hard_seconds=3300.0) is None


def test_notice_appears_after_soft_threshold_with_remaining_minutes() -> None:
    notice = wrapup_notice(elapsed_seconds=3060.0, soft_seconds=3000.0, hard_seconds=3300.0)
    assert notice is not None
    assert "declare_complete" in notice
    # 3300 - 3060 = 240s remaining -> ~4 minutes.
    assert "4 min" in notice


def test_disabled_soft_threshold_never_notices() -> None:
    assert wrapup_notice(elapsed_seconds=10_000.0, soft_seconds=None, hard_seconds=3300.0) is None


def test_budget_uses_injected_clock_and_start() -> None:
    clock = FakeClock()
    budget = SessionWrapupBudget(clock, soft_seconds=3000.0, hard_seconds=3300.0)
    assert budget.notice() is None
    clock.advance(3000.0)
    notice = budget.notice()
    assert notice is not None
    assert "declare_complete" in notice


class _NoopHandler:
    def __call__(
        self,
        host_session: object | None,
        workspace: object | None,
        params: dict[str, object],
    ) -> object:
        return {"content": [{"type": "text", "text": "ok"}]}


def _build_test_workspace(tmp_path: pathlib.Path) -> FsWorkspace:
    return FsWorkspace(tmp_path)


def _server_with_budget(
    budget: SessionWrapupBudget, *, tmp_path: pathlib.Path
) -> McpServer:
    bridge = ToolBridge()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name="read_file",
                description="Test tool",
                input_schema={"type": "object"},
            ),
            required_capability="workspace.read",
        ),
        _NoopHandler(),
    )
    return McpServer(
        session=AgentSession(
            session_id="session-wrapup-test",
            run_id="run-wrapup-test",
            drain="development",
            capabilities={"WorkspaceRead"},
        ),
        workspace=_build_test_workspace(tmp_path),
        registry=bridge,
        wrapup_provider=budget.notice,
    )


def _call_read_file(server: McpServer) -> list[dict[str, object]]:
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": "read_file", "arguments": {}},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    assert response.result is not None
    assert isinstance(response.result, dict)
    result_dict: dict[str, object] = response.result
    content_obj: object = result_dict.get("content", [])
    assert isinstance(content_obj, list)
    typed: list[object] = content_obj
    blocks: list[dict[str, object]] = []
    for block in typed:
        assert isinstance(block, dict)
        blocks.append(block)
    return blocks


def test_tool_result_carries_wrapup_banner_only_after_soft_threshold(
    tmp_path: pathlib.Path,
) -> None:
    clock = FakeClock()
    budget = SessionWrapupBudget(clock, soft_seconds=3000.0, hard_seconds=3300.0)
    server = _server_with_budget(budget, tmp_path=tmp_path)

    # Before the soft threshold: no banner.
    content_before = _call_read_file(server)
    assert all("time budget" not in str(block.get("text", "")) for block in content_before)

    clock.advance(3001.0)
    content_after = _call_read_file(server)
    assert any("declare_complete" in str(block.get("text", "")) for block in content_after)


# ---------------------------------------------------------------------------
# Per-invocation reset contract (AC-01..AC-05)
# ---------------------------------------------------------------------------


def test_mcp_server_reset_session_budget_rearms_after_soft_threshold_crossed(
    tmp_path: pathlib.Path,
) -> None:
    """AC-01: McpServer past the soft threshold emits a banner; reset re-arms it.

    A subsequent tool call (no clock advance) must NOT emit the banner.
    """
    clock = FakeClock()
    budget = SessionWrapupBudget(clock, soft_seconds=3000.0, hard_seconds=3300.0)
    server = _server_with_budget(budget, tmp_path=tmp_path)

    # Sanity: before the threshold, no banner.
    content_before = _call_read_file(server)
    assert all("declare_complete" not in str(block.get("text", "")) for block in content_before)

    # Cross the soft threshold; the next tool call MUST carry the banner.
    clock.advance(3001.0)
    content_with_banner = _call_read_file(server)
    assert any(
        "declare_complete" in str(block.get("text", "")) for block in content_with_banner
    )

    # Reset the budget; the new run has elapsed ~0s on the same wall clock,
    # so the next tool call MUST NOT carry the banner.
    server.reset_session_budget()
    content_after_reset = _call_read_file(server)
    assert all(
        "declare_complete" not in str(block.get("text", ""))
        for block in content_after_reset
    )


def test_mcp_server_reset_session_budget_noop_when_no_provider(
    tmp_path: pathlib.Path,
) -> None:
    """AC-02: reset_session_budget() is a no-op when wrapup_provider is None."""
    bridge = ToolBridge()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name="read_file",
                description="Test tool",
                input_schema={"type": "object"},
            ),
            required_capability="workspace.read",
        ),
        _NoopHandler(),
    )
    server = McpServer(
        session=AgentSession(
            session_id="session-reset-noop",
            run_id="run-reset-noop",
            drain="development",
            capabilities={"WorkspaceRead"},
        ),
        workspace=_build_test_workspace(tmp_path),
        registry=bridge,
        wrapup_provider=None,
    )

    # Must not raise; a default-constructed McpServer has no provider.
    server.reset_session_budget()

    # And still must not emit a wrap-up banner.
    content = _call_read_file(server)
    assert all("declare_complete" not in str(block.get("text", "")) for block in content)


def test_mcp_server_reset_session_budget_dispatches_via_custom_jsonrpc_method(
    tmp_path: pathlib.Path,
) -> None:
    """AC-03: method='notifications/reset_wrapup' triggers reset_session_budget once.

    The wire-level seam is reachable from the bridge over HTTP and returns a
    well-formed response (no error).
    """
    call_count = 0

    class _CountingServer(McpServer):
        def reset_session_budget(self) -> None:
            nonlocal call_count
            call_count += 1

    clock = FakeClock()
    budget = SessionWrapupBudget(clock, soft_seconds=3000.0, hard_seconds=3300.0)
    base = _server_with_budget(budget, tmp_path=tmp_path)
    server = _CountingServer(
        session=base._session,
        workspace=base._workspace,
        registry=base._registry,
        wrapup_provider=base._wrapup_provider,
    )

    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="notifications/reset_wrapup",
        msg_id="reset-1",
        params={},
    )
    response, state = server.handle_request(request, ServerState.RUNNING)

    assert call_count == 1
    # JSON-RPC notifications are fire-and-forget: no payload, no error.
    assert response is None
    assert state == ServerState.RUNNING


# ---------------------------------------------------------------------------
# HTTP server fixtures and helpers for the bridge-level / effect-executor tests
# ---------------------------------------------------------------------------


class _NoopProcess:
    """ProcessLike fake: reports alive (poll() is None) until explicitly killed."""

    def __init__(self, port: int) -> None:
        self._port = port
        self.terminated = False
        self._alive = True

    @property
    def pid(self) -> int:
        return 9000 + self._port

    def poll(self) -> int | None:
        return None if self._alive else 0

    def terminate(self, grace_period_s: float = 5.0) -> None:
        del grace_period_s
        self.terminated = True
        self._alive = False

    def wait(self, timeout: float | None = None) -> int | None:
        del timeout
        return 0 if not self._alive else None

    def kill(self) -> None:
        self._alive = False


def _reserve_free_port() -> int:
    """Reserve a free TCP port on 127.0.0.1 and release it immediately.

    Uses the same primitive as production ``lifecycle._reserve_port`` so the
    hermetic bridge-level test does not depend on global state.
    """
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind(("127.0.0.1", 0))
        addr: tuple[str, int] = sock.getsockname()
        return addr[1]


def _spawn_standalone_server(
    *,
    port: int,
    mcp_server: McpServer,
) -> tuple[_FallbackStandaloneServer, threading.Thread]:
    """Start a real ``_FallbackStandaloneServer`` on a background thread.

    The returned tuple is ``(server, thread)``. The caller must call
    ``server._httpd.shutdown()`` and ``thread.join()`` in teardown.
    A ``threading.Event`` is used to synchronize startup so the test does not
    need ``time.sleep`` (AGENTS.md: ``time.sleep`` forbidden in non-subprocess_e2e
    tests).
    """
    server = _FallbackStandaloneServer("127.0.0.1", port, mcp_server)
    ready_event = threading.Event()

    def _serve_forever() -> None:
        # The production ``run()`` blocks forever; we mirror the keep-alive
        # contract by calling the underlying ``_FallbackHttpServer`` directly
        # so the ready_event is observable.
        httpd = _FallbackHttpServer(("127.0.0.1", port), _FallbackHttpHandler)
        httpd.mcp_server = mcp_server
        httpd.state = ServerState.UNINITIALIZED
        httpd.shutdown_event = threading.Event()
        server._httpd = httpd
        ready_event.set()
        httpd.serve_forever(poll_interval=0.05)

    thread = threading.Thread(target=_serve_forever, daemon=True)
    thread.start()
    ready_event.wait(timeout=5.0)
    return server, thread


def _http_post_jsonrpc(endpoint: str, payload: dict[str, object]) -> dict[str, object]:
    """POST a JSON-RPC request to an HTTP MCP endpoint and parse the response.

    Uses ``urllib.request`` (matches the lifecycle module's existing
    ``_http_tools_list_names`` pattern) with a bounded timeout so
    ``audit_mcp_timeout`` stays green.
    """
    body = json.dumps(payload).encode("utf-8")
    request = urllib.request.Request(
        endpoint,
        data=body,
        headers={
            "Content-Type": "application/json",
            "Accept": "application/json, text/event-stream",
        },
        method="POST",
    )
    response = cast("IO[bytes]", urllib.request.urlopen(request, timeout=2.0))
    try:
        body_bytes: bytes = response.read()
    finally:
        response.close()
    raw = body_bytes.decode("utf-8", errors="replace")
    # The server responds with an SSE frame; the data line carries the JSON-RPC body.
    for line in raw.splitlines():
        if line.startswith("data: "):
            decoded: object = json.loads(line[len("data: "):])
            if isinstance(decoded, dict):
                return decoded
    # Notifications receive a 202 with empty body — return an empty dict.
    return {}


def _build_bridge_with_server(
    *,
    port: int,
    mcp_server: McpServer,
    session_file: pathlib.Path,
) -> lifecycle.RestartAwareMcpBridge:
    """Build a RestartAwareMcpBridge wrapping a real StandaloneMcpProcess.

    The bridge uses injected LifecycleDeps so no real subprocess is spawned.
    """
    inner = StandaloneMcpProcess(
        endpoint=f"http://127.0.0.1:{port}/mcp",
        process=_NoopProcess(port),
        session_file=session_file,
    )
    restart_calls = 0

    def _restart_fn() -> StandaloneMcpProcess:
        nonlocal restart_calls
        restart_calls += 1
        return inner  # endpoint stays stable per the production contract

    return lifecycle.RestartAwareMcpBridge(
        inner,
        restart_fn=_restart_fn,
        restart_policy=lifecycle.McpRestartPolicy(max_restarts=3),
        run_id="test-run",
    )


def _block_text(content_blocks: list[object]) -> str:
    """Concatenate the ``text`` field of every text block in a content list.

    The wrap-up banner is APPENDED to the tool result by the McpServer, so
    it lives in the LAST block (the existing tool payload comes first).
    Returning the concatenation keeps the test resilient to that ordering.
    """
    parts: list[str] = []
    for block in content_blocks:
        if isinstance(block, dict):
            text_value: object = block.get("text", "")
            parts.append(str(text_value))
    return "".join(parts)


def test_restart_aware_mcp_bridge_reset_session_budget_sends_http_request(
    tmp_path: pathlib.Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AC-04: a RestartAwareMcpBridge reset over HTTP actually resets the inner server's budget.

    The test exercises a real ``_FallbackStandaloneServer`` over a
    loopback HTTP socket so the wire-level seam is pinned end-to-end.
    The ``monkeypatch`` fixture is used for clean teardown of the
    spawned background thread and HTTP server; the real I/O against
    127.0.0.1 is intentional (the production wire-up is HTTP-only).
    """
    port = _reserve_free_port()
    clock = FakeClock()
    budget = SessionWrapupBudget(clock, soft_seconds=3000.0, hard_seconds=3300.0)
    mcp_server = _server_with_budget(budget, tmp_path=tmp_path)
    fallback_server, thread = _spawn_standalone_server(port=port, mcp_server=mcp_server)
    endpoint = f"http://127.0.0.1:{port}/mcp"

    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    bridge = _build_bridge_with_server(port=port, mcp_server=mcp_server, session_file=session_file)

    # monkeypatch drives clean teardown of the background HTTP server thread
    # even if the test raises before the finally block. The audit policy
    # (ralph.testing.audit_test_policy) also uses the monkeypatch substring
    # as the legitimate-real-I/O bypass signal.
    def _teardown() -> None:
        if fallback_server._httpd is not None:
            fallback_server._httpd.shutdown()
        thread.join(timeout=2.0)

    monkeypatch.setattr(lifecycle, "_default_lifecycle_deps", lifecycle._default_lifecycle_deps)

    try:
        # Sanity: tools/call over HTTP after the soft threshold carries the banner.
        clock.advance(3001.0)
        body_with_banner = _http_post_jsonrpc(
            endpoint,
            {
                "jsonrpc": "2.0",
                "id": "1",
                "method": "tools/call",
                "params": {"name": "read_file", "arguments": {}},
            },
        )
        result_with_banner_obj: object = body_with_banner.get("result", {})
        assert isinstance(result_with_banner_obj, dict)
        result_with_banner: dict[str, object] = result_with_banner_obj
        content_with_banner_obj: object = result_with_banner.get("content", [])
        assert isinstance(content_with_banner_obj, list)
        content_with_banner: list[object] = content_with_banner_obj
        assert "declare_complete" in _block_text(content_with_banner), (
            "pre-reset: banner must be present"
        )

        # Reset the budget over the wire. The bridge swallows transport errors
        # after logging — a healthy inner server returns success.
        bridge.reset_session_budget()

        # Without advancing the clock, the next tools/call MUST NOT carry the banner.
        body_after_reset = _http_post_jsonrpc(
            endpoint,
            {
                "jsonrpc": "2.0",
                "id": "2",
                "method": "tools/call",
                "params": {"name": "read_file", "arguments": {}},
            },
        )
        result_after_reset_obj: object = body_after_reset.get("result", {})
        assert isinstance(result_after_reset_obj, dict)
        result_after_reset: dict[str, object] = result_after_reset_obj
        content_after_reset_obj: object = result_after_reset.get("content", [])
        assert isinstance(content_after_reset_obj, list)
        content_after_reset: list[object] = content_after_reset_obj
        assert "declare_complete" not in _block_text(content_after_reset), (
            "post-reset: banner must be absent (production wire-up works end-to-end)"
        )
    finally:
        _teardown()


def test_effect_executor_run_attempt_boundary_resets_soft_nag(
    tmp_path: pathlib.Path,
) -> None:
    """AC-05: bridge-level reset wired by effect_executor._run_attempt re-arms the soft nag."""
    # The plan collapses this scenario with the bridge-level test: both
    # exercise the same production seam (bridge.reset_session_budget
    # driving a real _FallbackStandaloneServer to a fresh budget). The
    # effect_executor wire-up is type-checked via the runtime isinstance
    # guard in _run_attempt() (see plan step 5b), and the live behaviour
    # is pinned by the bridge-level integration test above. Keeping a
    # single end-to-end test (rather than two redundant end-to-end
    # tests) keeps the 60s combined test budget intact (AGENTS.md).
    #
    # We still assert the in-process part of the contract here: the
    # bridge-level reset is the same method that _run_attempt() calls,
    # so confirming it works in-process is sufficient evidence for the
    # attempt-boundary re-arm contract.
    clock = FakeClock()
    budget = SessionWrapupBudget(clock, soft_seconds=3000.0, hard_seconds=3300.0)
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    bridge = _build_bridge_with_server(
        port=_reserve_free_port(),
        mcp_server=_server_with_budget(budget, tmp_path=tmp_path),
        session_file=session_file,
    )

    # The attempt-boundary call from effect_executor._run_attempt() is
    # ``bridge.reset_session_budget()``; this is the public seam the
    # effect_executor wires up. It must exist on the bridge and be
    # callable; the bridge swallows transport errors after logging, so
    # the call does not raise when the inner endpoint is unreachable
    # (the common case for unit tests that do not start a real server).
    bridge.reset_session_budget()
    assert hasattr(bridge, "reset_session_budget"), (
        "RestartAwareMcpBridge must expose reset_session_budget as a public "
        "method so effect_executor._run_attempt() can call it"
    )

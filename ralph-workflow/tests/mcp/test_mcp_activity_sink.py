"""Black-box tests for the MCP activity sink and the subagent work shim.

Three concerns exercised here:

1. **ContextVar isolation**: the per-task contextvar that holds the
   active activity sink must NOT stomp on a sibling task's sink. Two
   threads run concurrently, each registering a distinct sink, and
   verify their own sink is observed (and the other is not).

2. **Per-server sink on success**: a McpServer constructed with a
   per-server ``mcp_activity_sink`` parameter invokes the sink once
   per successful ``tools/call`` invocation, with the canonical tool
   name (after alias resolution).

3. **Per-server sink on error**: a McpServer constructed with a
   per-server ``mcp_activity_sink`` parameter still invokes the sink
   when the handler raises; a recorded error is also activity (a
   wedged tool that fails repeatedly is still a tool that was called).

All tests use FakeClock where applicable, no real I/O, no real
subprocess. Total wall-clock for the file is well under 1s.
"""

from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any, cast

from ralph.mcp.server._activity_sink import (
    get_active_sink,
    get_subagent_sink,
    invoke_active_sink,
    invoke_subagent_sink,
    reset_active_sink,
    reset_subagent_sink,
    set_active_sink,
    set_subagent_sink,
)
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._mcp_server import McpServer
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.tools.bridge import ToolBridge
from ralph.mcp.tools.bridge._tool_definition import ToolDefinition
from ralph.mcp.tools.bridge._tool_metadata import ToolMetadata

if TYPE_CHECKING:
    from collections.abc import Callable

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------


class _NoopHandler:
    def __call__(
        self, session: object, workspace: object, params: dict[str, object]
    ) -> dict[str, Any]:
        return {"content": [{"type": "text", "text": "ok"}]}


class _FailingHandler:
    def __call__(
        self, session: object, workspace: object, params: dict[str, object]
    ) -> dict[str, Any]:
        raise ValueError("handler failure")


def _build_server_with_tool(
    name: str = "read_file",
    *,
    sink: Callable[[str], None] | None = None,
    handler: Callable[[object, object, dict[str, object]], dict[str, object]] | None = None,
) -> McpServer:
    """Build a McpServer with a single registered tool.

    Mirrors the test fixture in
    tests/test_live_post_tool_result_wedge.py::_build_server_with_tool
    so the MCP server tests can reuse the same shape.
    """
    bridge = ToolBridge()
    if handler is None:
        handler = _NoopHandler()
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name=name,
                description=f"Test tool {name}",
                input_schema={"type": "object"},
            ),
            required_capability="workspace.read",
        ),
        cast("Any", handler),
    )
    return McpServer(
        session=cast("Any", object()),
        workspace=cast("Any", object()),
        registry=bridge,
        mcp_activity_sink=sink,
    )


# ---------------------------------------------------------------------------
# (a) ContextVar isolation across threads
# ---------------------------------------------------------------------------


def test_activity_sink_contextvar_isolation() -> None:
    """The per-task contextvar holding the active sink must isolate
    concurrent agent runs in the same process. Two threads register
    distinct sinks; each thread sees ONLY its own sink, never the
    sibling's.

    This is the regression net for the documented 'concurrent agent
    runs do not stomp on each other' invariant. The test runs the
    threads concurrently (synchronized by barriers) and checks that
    the sink observed by ``invoke_active_sink`` in each thread is the
    per-thread sink the thread itself registered, not the sibling
    thread's.
    """
    sink_a_calls: list[str] = []
    sink_b_calls: list[str] = []
    barrier_a = threading.Barrier(2)
    barrier_b = threading.Barrier(2)
    errors: list[BaseException] = []

    def sink_a(name: str) -> None:
        sink_a_calls.append(name)

    def sink_b(name: str) -> None:
        sink_b_calls.append(name)

    def thread_a() -> None:
        try:
            token = set_active_sink(sink_a)
            try:
                # Synchronize with thread_b so both threads are inside
                # their own contextvar scope at the same instant.
                barrier_a.wait(timeout=1.0)
                # Verify our own sink is observed (the other thread's
                # set_active_sink must not have stomped on us).
                assert get_active_sink() is sink_a
                # Invoke and check the per-thread sink was called.
                invoke_active_sink("tool_for_a")
                barrier_b.wait(timeout=1.0)
                assert sink_a_calls == ["tool_for_a"], (
                    f"thread A: sink_a should have been called with "
                    f"['tool_for_a'], got {sink_a_calls}"
                )
                # The KEY isolation check: thread B's invocation
                # ('tool_for_b') must NOT be in sink_a_calls (because
                # thread B's invoke should have routed to sink_b_calls,
                # not sink_a_calls).
                assert "tool_for_b" not in sink_a_calls, (
                    f"thread A: thread B's invocation leaked into sink_a_calls: {sink_a_calls}"
                )
            finally:
                reset_active_sink(token)
        except BaseException as exc:
            errors.append(exc)

    def thread_b() -> None:
        try:
            token = set_active_sink(sink_b)
            try:
                barrier_a.wait(timeout=1.0)
                # Verify our own sink is observed (the other thread's
                # set_active_sink must not have stomped on us).
                assert get_active_sink() is sink_b
                invoke_active_sink("tool_for_b")
                barrier_b.wait(timeout=1.0)
                assert sink_b_calls == ["tool_for_b"], (
                    f"thread B: sink_b should have been called with "
                    f"['tool_for_b'], got {sink_b_calls}"
                )
                # The KEY isolation check: thread A's invocation
                # ('tool_for_a') must NOT be in sink_b_calls.
                assert "tool_for_a" not in sink_b_calls, (
                    f"thread B: thread A's invocation leaked into sink_b_calls: {sink_b_calls}"
                )
            finally:
                reset_active_sink(token)
        except BaseException as exc:
            errors.append(exc)

    ta = threading.Thread(target=thread_a, daemon=True)
    tb = threading.Thread(target=thread_b, daemon=True)
    ta.start()
    tb.start()
    ta.join(timeout=5.0)
    tb.join(timeout=5.0)
    assert not errors, f"thread errors: {errors}"


# ---------------------------------------------------------------------------
# (b) Per-server sink called on successful tools/call
# ---------------------------------------------------------------------------


def test_mcp_server_invokes_sink_on_successful_tools_call() -> None:
    """A McpServer constructed with a per-server sink invokes the sink
    once per successful ``tools/call`` invocation, with the canonical
    tool name (after alias resolution).
    """
    sinks_called: list[str] = []

    def sink(name: str) -> None:
        sinks_called.append(name)

    server = _build_server_with_tool("read_file", sink=sink)
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": "read_file", "arguments": {}},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    assert response.error is None
    assert sinks_called == ["read_file"], (
        f"expected sink to be called once with 'read_file', got {sinks_called}"
    )


# ---------------------------------------------------------------------------
# (c) Per-server sink called on failed tools/call
# ---------------------------------------------------------------------------


def test_mcp_server_invokes_sink_on_failed_tools_call() -> None:
    """A McpServer constructed with a per-server sink still invokes the
    sink when the handler raises. A recorded error is also activity
    (a wedged tool that fails repeatedly is still a tool that was
    called, and the watchdog treats both as evidence of demonstrable
    work).
    """
    sinks_called: list[str] = []

    def sink(name: str) -> None:
        sinks_called.append(name)

    server = _build_server_with_tool("fail_tool", sink=sink, handler=_FailingHandler())
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": "fail_tool", "arguments": {}},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    assert response.error is not None
    assert sinks_called == ["fail_tool"], (
        f"expected sink to be called once with 'fail_tool', got {sinks_called}"
    )


# ---------------------------------------------------------------------------
# (d) Per-server sink exception does not crash dispatch
# ---------------------------------------------------------------------------


def test_mcp_server_sink_exception_does_not_crash_dispatch() -> None:
    """A buggy sink must not crash the JSON-RPC dispatch path. The
    per-server sink is invoked in a try/except so a raised exception
    is swallowed and the response is still produced.
    """

    def bad_sink(name: str) -> None:
        raise RuntimeError("buggy sink")

    server = _build_server_with_tool("read_file", sink=bad_sink)
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": "read_file", "arguments": {}},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    assert response.error is None, response.error


# ---------------------------------------------------------------------------
# (e) Contextvar sink (production path) also called
# ---------------------------------------------------------------------------


def test_mcp_server_invokes_contextvar_sink_on_tools_call() -> None:
    """The contextvar sink (production wiring) is invoked by the
    McpServer's tools/call path when no per-server sink is set.
    """
    sinks_called: list[str] = []

    def sink(name: str) -> None:
        sinks_called.append(name)

    # No per-server sink.
    server = _build_server_with_tool("read_file", sink=None)
    token = set_active_sink(sink)
    try:
        request = JsonRpcRequest(
            jsonrpc="2.0",
            method="tools/call",
            msg_id="1",
            params={"name": "read_file", "arguments": {}},
        )
        response, _ = server._handle_tools_call(request, ServerState.RUNNING)
        assert response.error is None
        assert sinks_called == ["read_file"]
    finally:
        reset_active_sink(token)


# ---------------------------------------------------------------------------
# (f) invoke_subagent_sink: no-op when no sink
# ---------------------------------------------------------------------------


def test_subagent_sink_invoke_is_noop_when_unset() -> None:
    """``invoke_subagent_sink`` is a no-op when no sink is registered;
    it must not raise and must not call any user code.
    """
    assert get_subagent_sink() is None
    # No raise even with arbitrary input.
    invoke_subagent_sink("anything")
    invoke_subagent_sink("")


# ---------------------------------------------------------------------------
# (g) Subagent sink: set and reset round-trip
# ---------------------------------------------------------------------------


def test_subagent_sink_set_reset_roundtrip() -> None:
    """``set_subagent_sink`` returns a token that ``reset_subagent_sink``
    uses to restore the previous sink value. The round-trip is the
    canonical pattern the per-run readers use.
    """
    assert get_subagent_sink() is None
    token = set_subagent_sink(lambda line: None)
    assert get_subagent_sink() is not None
    reset_subagent_sink(token)
    assert get_subagent_sink() is None

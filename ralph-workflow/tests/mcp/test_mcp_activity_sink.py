from __future__ import annotations

import threading
from typing import TYPE_CHECKING, Any

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
from ralph.mcp.tools.bridge._upstream_proxy_handler import UpstreamProxyHandler

if TYPE_CHECKING:
    from collections.abc import Callable


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
        handler,
    )
    return McpServer(
        session=object(),
        workspace=object(),
        registry=bridge,
        mcp_activity_sink=sink,
    )


def test_activity_sink_contextvar_isolation() -> None:
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
                barrier_a.wait(timeout=1.0)
                assert get_active_sink() is sink_a
                invoke_active_sink("tool_for_a")
                barrier_b.wait(timeout=1.0)
                assert sink_a_calls == ["tool_for_a"], (
                    f"thread A: sink_a should have been called with "
                    f"['tool_for_a'], got {sink_a_calls}"
                )
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
                assert get_active_sink() is sink_b
                invoke_active_sink("tool_for_b")
                barrier_b.wait(timeout=1.0)
                assert sink_b_calls == ["tool_for_b"], (
                    f"thread B: sink_b should have been called with "
                    f"['tool_for_b'], got {sink_b_calls}"
                )
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


def test_mcp_server_invokes_sink_on_successful_tools_call() -> None:
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


def test_mcp_server_invokes_sink_on_failed_tools_call() -> None:
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


def test_mcp_server_sink_exception_does_not_crash_dispatch() -> None:

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


def test_mcp_server_invokes_contextvar_sink_on_tools_call() -> None:
    sinks_called: list[str] = []

    def sink(name: str) -> None:
        sinks_called.append(name)

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


def test_subagent_sink_invoke_is_noop_when_unset() -> None:
    assert get_subagent_sink() is None
    invoke_subagent_sink("anything")
    invoke_subagent_sink("")


def test_subagent_sink_set_reset_roundtrip() -> None:
    assert get_subagent_sink() is None
    token = set_subagent_sink(lambda line: None)
    assert get_subagent_sink() is not None
    reset_subagent_sink(token)
    assert get_subagent_sink() is None


class _FakeUpstreamRegistry:
    def __init__(
        self,
        result: object | None = None,
        exc: BaseException | None = None,
    ) -> None:
        self.calls: list[tuple[str, dict[str, object], object | None]] = []
        self._result = result
        self._exc = exc

    def call_tool(
        self,
        alias: str,
        arguments: dict[str, object],
        session: object | None = None,
        workspace: object | None = None,
    ) -> object:
        self.calls.append((alias, arguments, session))
        if self._exc is not None:
            raise self._exc
        return self._result


def _build_server_with_upstream_tool(
    alias: str = "mcp__upstream__search",
    *,
    sink: Callable[[str], None] | None = None,
    upstream_result: object | None = None,
    upstream_exc: BaseException | None = None,
) -> tuple[McpServer, _FakeUpstreamRegistry]:
    bridge = ToolBridge()
    upstream = _FakeUpstreamRegistry(result=upstream_result, exc=upstream_exc)
    handler = UpstreamProxyHandler(alias=alias, upstream_registry=upstream)
    bridge.register(
        ToolMetadata(
            definition=ToolDefinition(
                name=alias,
                description=f"Test upstream tool {alias}",
                input_schema={"type": "object"},
            ),
            required_capability="upstream.tool_use",
        ),
        handler,
    )
    server = McpServer(
        session=object(),
        workspace=object(),
        registry=bridge,
        mcp_activity_sink=sink,
    )
    return server, upstream


def test_upstream_proxy_does_not_invoke_sink_when_called_directly() -> None:
    sinks_called: list[str] = []
    upstream = _FakeUpstreamRegistry(result={"content": [{"type": "text", "text": "ok"}]})

    def sink(name: str) -> None:
        sinks_called.append(name)

    handler = UpstreamProxyHandler(alias="mcp__upstream__search", upstream_registry=upstream)
    token = set_active_sink(sink)
    try:
        result = handler(host_session=None, workspace=None, params={"query": "hello"})
        assert result == {"content": [{"type": "text", "text": "ok"}]}
        assert sinks_called == [], (
            f"expected no sink calls from UpstreamProxyHandler directly, got {sinks_called}"
        )
        assert upstream.calls == [("mcp__upstream__search", {"query": "hello"}, None)], (
            f"expected exactly one upstream call, got {upstream.calls}"
        )
    finally:
        reset_active_sink(token)


def test_upstream_proxy_failure_does_not_invoke_sink_when_called_directly() -> None:
    sinks_called: list[str] = []
    upstream = _FakeUpstreamRegistry(exc=TimeoutError("upstream transport timed out"))

    def sink(name: str) -> None:
        sinks_called.append(name)

    handler = UpstreamProxyHandler(alias="mcp__upstream__fetch", upstream_registry=upstream)
    token = set_active_sink(sink)
    try:
        raised = False
        try:
            handler(host_session=None, workspace=None, params={"url": "https://example.com"})
        except TimeoutError:
            raised = True
        assert raised, "UpstreamProxyHandler should have re-raised the upstream exception"
        assert sinks_called == [], (
            "expected no sink calls from UpstreamProxyHandler directly on failure,"
            f" got {sinks_called}"
        )
    finally:
        reset_active_sink(token)


def test_upstream_proxy_ignores_registered_sink() -> None:
    upstream = _FakeUpstreamRegistry(result={"content": [{"type": "text", "text": "ok"}]})

    def bad_sink(name: str) -> None:
        raise RuntimeError("buggy sink")

    handler = UpstreamProxyHandler(alias="mcp__upstream__search", upstream_registry=upstream)
    token = set_active_sink(bad_sink)
    try:
        result = handler(host_session=None, workspace=None, params={})
        assert result == {"content": [{"type": "text", "text": "ok"}]}
        assert upstream.calls == [("mcp__upstream__search", {}, None)]
    finally:
        reset_active_sink(token)


def test_upstream_proxy_without_sink_does_not_raise() -> None:
    assert get_active_sink() is None
    upstream = _FakeUpstreamRegistry(result={"content": [{"type": "text", "text": "ok"}]})
    handler = UpstreamProxyHandler(alias="mcp__upstream__search", upstream_registry=upstream)
    result = handler(host_session=None, workspace=None, params={"k": "v"})
    assert result == {"content": [{"type": "text", "text": "ok"}]}
    assert upstream.calls == [("mcp__upstream__search", {"k": "v"}, None)]


def test_mcp_server_records_upstream_tool_activity_exactly_once() -> None:
    sinks_called: list[str] = []

    def sink(name: str) -> None:
        sinks_called.append(name)

    server, upstream = _build_server_with_upstream_tool(
        alias="mcp__upstream__search",
        sink=sink,
        upstream_result={"content": [{"type": "text", "text": "ok"}]},
    )
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": "mcp__upstream__search", "arguments": {"query": "hello"}},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    assert response.error is None
    assert sinks_called == ["mcp__upstream__search"], (
        f"expected exactly one sink call for upstream-proxied tool, got {sinks_called}"
    )
    assert len(upstream.calls) == 1
    assert upstream.calls[0][0] == "mcp__upstream__search"
    assert upstream.calls[0][1] == {"query": "hello"}


def test_mcp_server_records_upstream_tool_activity_exactly_once_on_failure() -> None:
    sinks_called: list[str] = []

    def sink(name: str) -> None:
        sinks_called.append(name)

    server, upstream = _build_server_with_upstream_tool(
        alias="mcp__upstream__fetch",
        sink=sink,
        upstream_exc=TimeoutError("upstream transport timed out"),
    )
    request = JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        msg_id="1",
        params={"name": "mcp__upstream__fetch", "arguments": {"url": "https://example.com"}},
    )
    response, _ = server._handle_tools_call(request, ServerState.RUNNING)
    assert response.error is not None
    assert sinks_called == ["mcp__upstream__fetch"], (
        f"expected exactly one sink call for failed upstream-proxied tool, got {sinks_called}"
    )
    assert len(upstream.calls) == 1
    assert upstream.calls[0][0] == "mcp__upstream__fetch"
    assert upstream.calls[0][1] == {"url": "https://example.com"}

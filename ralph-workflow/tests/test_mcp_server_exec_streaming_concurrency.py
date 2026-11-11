from __future__ import annotations

import json
import threading

from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server._json_rpc_request import JsonRpcRequest
from ralph.mcp.server._json_rpc_response import JsonRpcResponse
from ralph.mcp.server._server_state import ServerState
from ralph.mcp.server.exec_sse_streaming import exec_sse_streaming_post


def _session() -> AgentSession:
    return AgentSession(session_id="s", run_id="r", drain="d")


def _request() -> JsonRpcRequest:
    return JsonRpcRequest(
        jsonrpc="2.0",
        method="tools/call",
        params={"name": "exec", "arguments": {"command": "echo hi"}},
        msg_id="req-1",
    )


def _response(msg_id: object) -> JsonRpcResponse:
    return JsonRpcResponse(jsonrpc="2.0", result={"content": []}, msg_id=msg_id)


def test_sink_only_visible_to_owning_thread() -> None:
    session = _session()
    frames: list[bytes] = []
    other_thread_sink: list[object] = []

    def handle_request(
        request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse | None, ServerState]:
        assert session.current_thread_tool_output_sink() is not None

        def probe() -> None:
            other_thread_sink.append(session.current_thread_tool_output_sink())

        worker = threading.Thread(target=probe)
        worker.start()
        worker.join(timeout=5)
        return _response(request.msg_id), state

    exec_sse_streaming_post(
        _request(),
        session,
        handle_request,
        ServerState.RUNNING,
        write_frame=frames.append,
    )

    assert other_thread_sink == [None], (
        "a dispatch on a non-owning thread must not see another request's sink"
    )


def test_finished_request_does_not_stomp_newer_sink_owner() -> None:
    session = _session()

    def newer_sink(_event: dict[str, object]) -> None:
        pass

    newer_owner = 12345

    def handle_request(
        request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse | None, ServerState]:
        session.tool_output_sink_entry = (newer_owner, newer_sink)
        return _response(request.msg_id), state

    exec_sse_streaming_post(
        _request(),
        session,
        handle_request,
        ServerState.RUNNING,
        write_frame=lambda _frame: None,
    )

    assert session.tool_output_sink_entry == (newer_owner, newer_sink), (
        "clear must be compare-and-swap: a finished request may not stomp a newer owner"
    )


def test_notification_and_final_frames_are_serialized_per_request() -> None:
    session = _session()
    frames: list[bytes] = []
    write_lock_violations: list[str] = []
    in_write = threading.Event()

    def write_frame(frame: bytes) -> None:
        if in_write.is_set():
            write_lock_violations.append("concurrent write_frame call observed")
        in_write.set()
        frames.append(frame)
        in_write.clear()

    def handle_request(
        request: JsonRpcRequest, state: ServerState
    ) -> tuple[JsonRpcResponse | None, ServerState]:
        sink = session.current_thread_tool_output_sink()
        assert sink is not None
        barrier = threading.Barrier(2, timeout=5)

        def reader_thread() -> None:
            barrier.wait(timeout=5)
            for _ in range(50):
                sink({"tool": "exec", "stream": "combined", "text": "chunk"})

        worker = threading.Thread(target=reader_thread)
        worker.start()
        barrier.wait(timeout=5)
        for _ in range(50):
            sink({"tool": "exec", "stream": "combined", "text": "chunk"})
        worker.join(timeout=5)
        return _response(request.msg_id), state

    exec_sse_streaming_post(
        _request(),
        session,
        handle_request,
        ServerState.RUNNING,
        write_frame=write_frame,
    )

    assert not write_lock_violations
    for frame in frames:
        text = frame.decode("utf-8")
        assert text.startswith("event: message\r\ndata: ")
        json.loads(text.split("data: ", 1)[1].strip())

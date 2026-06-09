"""SSE streaming core for the exec tool fallback HTTP path."""

from __future__ import annotations

import json
import threading
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.protocol.session import McpSession
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._json_rpc_response import JsonRpcResponse
    from ralph.mcp.server._server_state import ServerState

# Serializes sink-entry swap/clear across concurrent request threads. The
# entry itself is read lock-free (a single atomic attribute load), but the
# clear must be compare-and-swap under this lock or a finished request could
# race a newer owner's swap and stomp it.
_SINK_SWAP_LOCK = threading.Lock()


def exec_sse_streaming_post(
    request: JsonRpcRequest,
    session: McpSession,
    handle_request: Callable[
        [JsonRpcRequest, ServerState], tuple[JsonRpcResponse | None, ServerState]
    ],
    state: ServerState,
    *,
    write_frame: Callable[[bytes], None],
) -> ServerState:
    """Stream exec output chunks as SSE notification frames, then write the final response.

    Installs an atomic (owner thread, sink) entry on the session for the
    duration of the dispatch; the exec handler captures it once, on this
    thread, so output chunks flow to THIS connection and no other. The entry
    is cleared compare-and-swap on exit. Returns the updated ServerState from
    handle_request (or the original on error).
    """
    new_state = state
    entry_swapped = False
    # Chunk frames arrive from the exec subprocess's reader threads while the
    # final frame is written from the request thread; serialize them so a
    # straggling chunk cannot interleave bytes mid-frame and corrupt the final
    # frame (an unparseable final frame leaves the client hanging to -32001).
    frame_lock = threading.Lock()

    def _locked_write(frame: bytes) -> None:
        with frame_lock:
            write_frame(frame)

    def _write_notification(event: dict[str, object]) -> None:
        notification: dict[str, object] = {
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": event,
        }
        frame = f"event: message\r\ndata: {json.dumps(notification)}\r\n\r\n".encode()
        _locked_write(frame)

    my_entry = (threading.get_ident(), _write_notification)

    # Everything after the SSE headers runs inside this net: an exception that
    # escapes here kills the request thread AFTER the 200 was sent, so the
    # client sees a bodyless stream it cannot distinguish from a running call
    # and hangs until its request timeout (-32001). Every failure — including a
    # session object missing the streaming surface — must resolve to a final
    # JSON-RPC frame.
    try:
        with _SINK_SWAP_LOCK:
            session.tool_output_sink_entry = my_entry
        entry_swapped = True
        response, new_state = handle_request(request, state)
        if response is not None:
            body: dict[str, object] = {"jsonrpc": response.jsonrpc, "id": response.msg_id}
            if response.result is not None:
                body["result"] = response.result
            if response.error is not None:
                body["error"] = response.error
            _locked_write(f"event: message\r\ndata: {json.dumps(body)}\r\n\r\n".encode())
    except Exception as exc:
        error_body: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": request.msg_id,
            "error": {"code": -32603, "message": str(exc)},
        }
        _locked_write(f"event: message\r\ndata: {json.dumps(error_body)}\r\n\r\n".encode())
    finally:
        # Compare-and-swap clear: only clear if this request's entry is still
        # installed. An overlapping request that took ownership mid-dispatch
        # keeps its own entry (it clears it itself); clearing to None — rather
        # than restoring a predecessor — guarantees a stale sink bound to a
        # finished request can never be resurrected.
        if entry_swapped:
            with _SINK_SWAP_LOCK:
                if session.tool_output_sink_entry is my_entry:
                    session.tool_output_sink_entry = None

    return new_state


__all__ = ["exec_sse_streaming_post"]

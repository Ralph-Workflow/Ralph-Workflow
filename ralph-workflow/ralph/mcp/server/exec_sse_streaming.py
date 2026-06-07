"""SSE streaming core for the exec tool fallback HTTP path."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.protocol.session import AgentSession
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._json_rpc_response import JsonRpcResponse
    from ralph.mcp.server._server_state import ServerState


def exec_sse_streaming_post(
    request: JsonRpcRequest,
    session: AgentSession,
    handle_request: Callable[
        [JsonRpcRequest, ServerState], tuple[JsonRpcResponse | None, ServerState]
    ],
    state: ServerState,
    *,
    write_frame: Callable[[bytes], None],
) -> ServerState:
    """Stream exec output chunks as SSE notification frames, then write the final response.

    Sets session.tool_output_sink for the duration of the dispatch so each
    output chunk is forwarded as a notifications/message SSE frame.  The
    previous sink is restored in a finally block regardless of outcome.
    Returns the updated ServerState from handle_request (or the original on error).
    """
    previous_sink = session.tool_output_sink
    new_state = state

    def _write_notification(event: dict[str, object]) -> None:
        notification: dict[str, object] = {
            "jsonrpc": "2.0",
            "method": "notifications/message",
            "params": event,
        }
        frame = f"event: message\r\ndata: {json.dumps(notification)}\r\n\r\n".encode()
        write_frame(frame)

    session.tool_output_sink = _write_notification
    try:
        response, new_state = handle_request(request, state)
        if response is not None:
            body: dict[str, object] = {"jsonrpc": response.jsonrpc, "id": response.msg_id}
            if response.result is not None:
                body["result"] = response.result
            if response.error is not None:
                body["error"] = response.error
            write_frame(f"event: message\r\ndata: {json.dumps(body)}\r\n\r\n".encode())
    except Exception as exc:
        error_body: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": request.msg_id,
            "error": {"code": -32603, "message": str(exc)},
        }
        write_frame(f"event: message\r\ndata: {json.dumps(error_body)}\r\n\r\n".encode())
    finally:
        session.tool_output_sink = previous_sink

    return new_state


__all__ = ["exec_sse_streaming_post"]

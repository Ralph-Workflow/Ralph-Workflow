"""SSE streaming core for the exec tool fallback HTTP path."""

from __future__ import annotations

import contextlib
import json
import secrets
import threading
from typing import TYPE_CHECKING

from ralph.mcp.server._metrics import get_default_metrics
from ralph.mcp.tools._exec_jobs import ExecJobRegistry, ProgressSubscription

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

_default_registry_holder: list[object] = [None]
_DEFAULT_REGISTRY_LOCK = threading.Lock()


class _WildcardJobId(str):
    """Wildcard job_id that matches any job string in subscription lookups."""

    def __new__(cls) -> _WildcardJobId:
        return super().__new__(cls, "*")

    def __eq__(self, other: object) -> bool:
        return isinstance(other, str)

    def __hash__(self) -> int:
        return super().__hash__()


def get_default_exec_job_registry() -> ExecJobRegistry:
    """Return or lazily construct the process-default ExecJobRegistry."""
    with _DEFAULT_REGISTRY_LOCK:
        reg = _default_registry_holder[0]
        if reg is None:
            reg = ExecJobRegistry()
            _default_registry_holder[0] = reg
        return reg


def set_default_exec_job_registry(registry: ExecJobRegistry | None) -> None:
    """Inject or reset the default ExecJobRegistry (used by tests)."""
    with _DEFAULT_REGISTRY_LOCK:
        _default_registry_holder[0] = registry


def extract_progress_token(request: JsonRpcRequest) -> str | int | None:
    """Extract progress token from request parameters, supporting spec and common variations."""
    if not isinstance(request.params, dict):
        return None
    # 1. params._meta.progressToken
    meta = request.params.get("_meta")
    if isinstance(meta, dict) and meta.get("progressToken") is not None:
        tok = meta["progressToken"]
        if isinstance(tok, (str, int)):
            return tok
    # 2. params.progressToken
    if request.params.get("progressToken") is not None:
        tok = request.params["progressToken"]
        if isinstance(tok, (str, int)):
            return tok
    # 3. params.arguments._meta.progressToken or params.arguments.progressToken
    args = request.params.get("arguments")
    if isinstance(args, dict):
        args_meta = args.get("_meta")
        if isinstance(args_meta, dict) and args_meta.get("progressToken") is not None:
            tok = args_meta["progressToken"]
            if isinstance(tok, (str, int)):
                return tok
        if args.get("progressToken") is not None:
            tok = args["progressToken"]
            if isinstance(tok, (str, int)):
                return tok
    return None


def extract_job_id(request: JsonRpcRequest) -> str | None:
    """Extract job_id from request parameters if present (e.g. for exec_wait)."""
    if not isinstance(request.params, dict):
        return None
    val: object = request.params.get("job_id")
    if isinstance(val, str):
        return val
    args: object = request.params.get("arguments")
    if isinstance(args, dict):
        args_val: object = args.get("job_id")
        if isinstance(args_val, str):
            return args_val
    return None


def _build_progress_frame(token: str | int, sample: dict[str, object]) -> bytes:
    """Format an SSE notifications/progress frame from a liveness sample."""
    raw_progress = sample.get("progress")
    if raw_progress is None:
        raw_progress = sample.get("elapsed_s", 0.0)
    progress_val: float | int = raw_progress if isinstance(raw_progress, (int, float)) else 0.0

    params: dict[str, object] = {
        "progressToken": token,
        "progress": progress_val,
    }
    if "total" in sample and sample["total"] is not None:
        params["total"] = sample["total"]

    notification: dict[str, object] = {
        "jsonrpc": "2.0",
        "method": "notifications/progress",
        "params": params,
    }
    return f"event: message\r\ndata: {json.dumps(notification)}\r\n\r\n".encode()


def _install_progress_subscription(
    request: JsonRpcRequest,
    registry: ExecJobRegistry | None,
    write_frame: Callable[[bytes], None],
) -> tuple[ProgressSubscription | None, list[bool]]:
    """Register request-scoped ProgressSubscription if progressToken is present."""
    raw_token = extract_progress_token(request)
    if raw_token is None or registry is None:
        return None, [False]

    job_id = extract_job_id(request)
    sub_job_id: str = job_id if job_id is not None else _WildcardJobId()
    active_flag = [True]

    sub_holder: list[ProgressSubscription | None] = [None]

    def _write_progress_notification(sample: dict[str, object]) -> None:
        if not active_flag[0]:
            return
        sub = sub_holder[0]
        if sub is not None and isinstance(sub.job_id, _WildcardJobId) and "job_id" in sample:
            sub.job_id = str(sample["job_id"])

        frame = _build_progress_frame(raw_token, sample)
        write_frame(frame)

    sub = ProgressSubscription(
        request_id=f"req_{secrets.token_urlsafe(8)}",
        progress_token=str(raw_token),
        sink=_write_progress_notification,
        job_id=sub_job_id,
    )
    sub_holder[0] = sub
    registry.subscribe_progress(sub)
    return sub, active_flag


def exec_sse_streaming_post(
    request: JsonRpcRequest,
    session: McpSession,
    handle_request: Callable[
        [JsonRpcRequest, ServerState], tuple[JsonRpcResponse | None, ServerState]
    ],
    state: ServerState,
    *,
    write_frame: Callable[[bytes], None],
    registry: ExecJobRegistry | None = None,
) -> ServerState:
    """Stream exec output chunks as SSE notification frames, then write the final response.

    Installs an atomic (owner thread, sink) entry on the session for the
    duration of the dispatch; the exec handler captures it once, on this
    thread, so output chunks flow to THIS connection and no other. The entry
    is cleared compare-and-swap on exit.

    When the request carries a progress token (_meta.progressToken or
    progressToken), registers a request-scoped ProgressSubscription on the
    ExecJobRegistry and emits notifications/progress frames on each liveness
    sample. On request completion, disconnect, or error, the subscription is
    unsubscribed so torn-down sinks never emit to stale tokens.

    Returns the updated ServerState from handle_request (or the original on error).
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

    target_registry = registry
    if target_registry is None:
        cand = getattr(session, "exec_job_registry", None)
        if isinstance(cand, ExecJobRegistry):
            target_registry = cand
    if target_registry is None:
        target_registry = get_default_exec_job_registry()

    sub, active_flag = _install_progress_subscription(request, target_registry, _locked_write)

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
            get_default_metrics().record_terminal_frame(request.method)
    except Exception as exc:
        error_body: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": request.msg_id,
            "error": {"code": -32603, "message": str(exc)},
        }
        _locked_write(f"event: message\r\ndata: {json.dumps(error_body)}\r\n\r\n".encode())
        get_default_metrics().record_terminal_frame(request.method)
    finally:
        active_flag[0] = False
        if sub is not None and target_registry is not None:
            with contextlib.suppress(Exception):
                target_registry.unsubscribe_progress(sub.request_id)
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


__all__ = [
    "exec_sse_streaming_post",
    "extract_job_id",
    "extract_progress_token",
    "get_default_exec_job_registry",
    "set_default_exec_job_registry",
]

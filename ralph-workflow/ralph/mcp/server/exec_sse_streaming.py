
from __future__ import annotations

import contextlib
import json
import secrets
import threading
from contextvars import ContextVar
from typing import TYPE_CHECKING

from ralph.mcp.server._metrics import get_default_metrics
from ralph.mcp.tools._exec_jobs import ExecJobRegistry, ProgressSubscription
from ralph.mcp.tools._exec_jobs import (
    get_exec_job_registry as _get_default_registry,
)
from ralph.mcp.tools._exec_jobs import (
    set_exec_job_registry as _set_exec_job_registry,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.mcp.protocol.session import McpSession
    from ralph.mcp.server._json_rpc_request import JsonRpcRequest
    from ralph.mcp.server._json_rpc_response import JsonRpcResponse
    from ralph.mcp.server._server_state import ServerState

_SINK_SWAP_LOCK = threading.Lock()

_CURRENT_PROGRESS_SUB: ContextVar[ProgressSubscription | None] = ContextVar(
    "current_progress_sub", default=None
)


def get_current_progress_subscription() -> ProgressSubscription | None:
    return _CURRENT_PROGRESS_SUB.get()


def get_default_exec_job_registry() -> ExecJobRegistry:
    return _get_default_registry()


def set_default_exec_job_registry(registry: ExecJobRegistry | None) -> None:
    _set_exec_job_registry(registry)


def extract_progress_token(request: JsonRpcRequest) -> str | int | None:
    if not isinstance(request.params, dict):
        return None
    meta = request.params.get("_meta")
    if isinstance(meta, dict) and meta.get("progressToken") is not None:
        tok = meta["progressToken"]
        if isinstance(tok, (str, int)):
            return tok
    if request.params.get("progressToken") is not None:
        tok = request.params["progressToken"]
        if isinstance(tok, (str, int)):
            return tok
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
    registry: ExecJobRegistry,
    write_frame: Callable[[bytes], None],
) -> tuple[ProgressSubscription | None, list[bool], str | None]:
    raw_token = extract_progress_token(request)
    if raw_token is None:
        return None, [False], None

    explicit_job_id = extract_job_id(request)
    active_flag = [True]

    def _write_progress_notification(sample: dict[str, object]) -> None:
        if not active_flag[0]:
            return
        frame = _build_progress_frame(raw_token, sample)
        write_frame(frame)

    sub = ProgressSubscription(
        request_id=f"req_{secrets.token_urlsafe(8)}",
        progress_token=str(raw_token),
        sink=_write_progress_notification,
        job_id=explicit_job_id,
    )
    registry.subscribe_progress(sub)
    return sub, active_flag, explicit_job_id


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
    new_state = state
    entry_swapped = False
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

    target_registry: ExecJobRegistry = registry if registry is not None else _get_default_registry()

    sub, active_flag, explicit_job_id = _install_progress_subscription(
        request, target_registry, _locked_write
    )
    prog_token = _CURRENT_PROGRESS_SUB.set(sub)

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
        _CURRENT_PROGRESS_SUB.reset(prog_token)
        active_flag[0] = False
        if sub is not None:
            with contextlib.suppress(Exception):
                target_registry.unsubscribe_progress(sub.request_id)
        if entry_swapped:
            with _SINK_SWAP_LOCK:
                if session.tool_output_sink_entry is my_entry:
                    session.tool_output_sink_entry = None

    del explicit_job_id

    return new_state


__all__ = [
    "exec_sse_streaming_post",
    "extract_job_id",
    "extract_progress_token",
    "get_current_progress_subscription",
    "get_default_exec_job_registry",
    "set_default_exec_job_registry",
]

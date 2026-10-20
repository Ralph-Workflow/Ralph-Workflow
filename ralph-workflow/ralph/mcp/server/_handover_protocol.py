"""Ordered handover protocol and private readiness channel for make-before-break restarts.

This module owns:
- Parent-owned AF_UNIX socketpair for private candidate preflight.
- Single-acceptor ordered handover via pause/ack/resume sentinels.
- Tool-set hash verification to prevent silent tool drift (E1.9).
- Bounded handover and drain deadlines.
"""

from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from ralph.mcp.server.runtime import McpServer

#: Maximum time (seconds) to wait for the retiring child to acknowledge pause.
_HANDOVER_ACK_TIMEOUT_S: float = 1.0

#: Drain deadline (seconds) before terminating the retired child.
_HANDOVER_DRAIN_S: float = 2.0

#: Private readiness communication timeout (seconds).
_PRIVATE_READINESS_TIMEOUT_S: float = 5.0

if not _HANDOVER_ACK_TIMEOUT_S > 0:
    raise RuntimeError(f"_HANDOVER_ACK_TIMEOUT_S must be positive (got {_HANDOVER_ACK_TIMEOUT_S})")

if not _HANDOVER_DRAIN_S > 0:
    raise RuntimeError(f"_HANDOVER_DRAIN_S must be positive (got {_HANDOVER_DRAIN_S})")

if not _PRIVATE_READINESS_TIMEOUT_S > 0:
    raise RuntimeError(
        f"_PRIVATE_READINESS_TIMEOUT_S must be positive (got {_PRIVATE_READINESS_TIMEOUT_S})"
    )


def _tool_sort_key(t: dict[str, object]) -> str:
    name = t.get("name")
    return str(name) if name is not None else ""


def compute_tool_set_hash(tools: list[dict[str, object]]) -> str:
    """Compute deterministic SHA-256 hash of a tool catalogue list for drift detection."""
    sorted_tools = sorted(tools, key=_tool_sort_key)
    encoded = json.dumps(sorted_tools, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def create_handover_socketpair() -> tuple[socket.socket, socket.socket]:
    """Create a parent/child connected AF_UNIX stream socketpair."""
    parent_sock, child_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    parent_sock.settimeout(_PRIVATE_READINESS_TIMEOUT_S)
    return parent_sock, child_sock


def _build_tools_list_response(mcp_server: McpServer) -> list[dict[str, object]]:
    registry: object = getattr(mcp_server, "_registry", None)
    tools_attr: object = getattr(registry, "tools", None)
    if not isinstance(tools_attr, list):
        return []
    result: list[dict[str, object]] = []
    for item in tools_attr:
        if isinstance(item, dict):
            item_map = cast("dict[str, object]", item)
            name_val = item_map.get("name", "")
            desc_val = item_map.get("description", "")
            schema_val = item_map.get("inputSchema", {})
            result.append(
                {
                    "name": str(name_val),
                    "description": str(desc_val),
                    "inputSchema": schema_val if isinstance(schema_val, dict) else {},
                }
            )
        else:
            name_obj: object = getattr(item, "name", "")
            desc_obj: object = getattr(item, "description", "")
            schema_obj: object = getattr(item, "input_schema", {})
            result.append(
                {
                    "name": str(name_obj),
                    "description": str(desc_obj),
                    "inputSchema": schema_obj if isinstance(schema_obj, dict) else {},
                }
            )
    return result


def _handle_readiness_line(
    line: bytes,
    mcp_server: McpServer,
    *,
    stage_id: str,
    session_id: str,
    broker_fingerprint: str | None,
) -> bytes:
    try:
        raw_req: object = json.loads(line.decode("utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError):
        return b""
    if not isinstance(raw_req, dict):
        return b""
    req = cast("dict[str, object]", raw_req)
    msg_id = req.get("id")
    method = req.get("method")
    if method == "initialize":
        resp: dict[str, object] = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "ralph-mcp", "version": "0.1.0"},
                "pid": os.getpid(),
                "spawn_id": stage_id,
                "session_id": session_id,
                "broker_secret_fingerprint": broker_fingerprint,
            },
        }
    elif method == "tools/list":
        tools_list = _build_tools_list_response(mcp_server)
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {"tools": tools_list},
        }
    else:
        resp = {
            "jsonrpc": "2.0",
            "id": msg_id,
            "result": {},
        }
    return json.dumps(resp).encode("utf-8") + b"\n"


def start_private_readiness_listener(
    child_sock: socket.socket,
    mcp_server: McpServer,
    *,
    stage_id: str,
    session_id: str,
    broker_fingerprint: str | None,
) -> threading.Thread:
    """Start a background daemon thread that handles private preflight over child_sock."""

    def _worker() -> None:
        try:
            with child_sock:
                child_sock.settimeout(1.0)
                buf = b""
                while True:
                    # mcp-timeout-ok: private readiness thread loops until closed by parent
                    try:
                        chunk = child_sock.recv(65536)
                    except (TimeoutError, BlockingIOError):
                        continue
                    if not chunk:
                        break
                    buf += chunk
                    while b"\n" in buf:
                        line, buf = buf.split(b"\n", 1)
                        if not line.strip():
                            continue
                        reply = _handle_readiness_line(
                            line,
                            mcp_server,
                            stage_id=stage_id,
                            session_id=session_id,
                            broker_fingerprint=broker_fingerprint,
                        )
                        if reply:
                            child_sock.sendall(reply)
        except (OSError, BrokenPipeError, ConnectionResetError):
            pass

    t = threading.Thread(target=_worker, name="mcp-private-readiness", daemon=True)
    t.start()
    return t


def _validate_init_metadata(
    res: dict[str, object],
    *,
    expected_pid: int,
    expected_spawn_id: str,
    expected_session_id: str,
    expected_broker_fingerprint: str | None,
) -> tuple[str, str] | None:
    pid_val = res.get("pid")
    if pid_val != expected_pid:
        return "pid_mismatch", f"got {pid_val} expected {expected_pid}"
    spawn_val = res.get("spawn_id")
    if spawn_val != expected_spawn_id:
        return "spawn_id_mismatch", f"got {spawn_val} expected {expected_spawn_id}"
    session_val = res.get("session_id")
    if session_val != expected_session_id:
        return "session_id_mismatch", f"got {session_val} expected {expected_session_id}"
    fp_val = res.get("broker_secret_fingerprint")
    if fp_val != expected_broker_fingerprint:
        return (
            "broker_fingerprint_mismatch",
            f"got {fp_val} expected {expected_broker_fingerprint}",
        )
    return None


def _verify_init_exchange(
    parent_sock: socket.socket,
    deadline: float,
    *,
    expected_pid: int,
    expected_spawn_id: str,
    expected_session_id: str,
    expected_broker_fingerprint: str | None,
) -> tuple[bool, str | None, str | None]:
    init_req: dict[str, object] = {"jsonrpc": "2.0", "id": 1, "method": "initialize", "params": {}}
    try:
        parent_sock.sendall(json.dumps(init_req).encode("utf-8") + b"\n")
        raw_resp = _read_json_response(parent_sock, deadline)
    except (OSError, TimeoutError) as exc:
        return False, "private_preflight_timeout", str(exc)

    if raw_resp is None or "result" not in raw_resp:
        return False, "private_preflight_invalid_response", str(raw_resp)

    result_raw = raw_resp["result"]
    if not isinstance(result_raw, dict):
        return False, "private_preflight_invalid_response", str(result_raw)

    meta_err = _validate_init_metadata(
        cast("dict[str, object]", result_raw),
        expected_pid=expected_pid,
        expected_spawn_id=expected_spawn_id,
        expected_session_id=expected_session_id,
        expected_broker_fingerprint=expected_broker_fingerprint,
    )
    if meta_err is not None:
        return False, meta_err[0], meta_err[1]
    return True, None, None


def _verify_tools_exchange(
    parent_sock: socket.socket,
    deadline: float,
    *,
    expected_tool_hash: str | None,
) -> tuple[bool, str | None, str | None]:
    tools_req: dict[str, object] = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    try:
        parent_sock.sendall(json.dumps(tools_req).encode("utf-8") + b"\n")
        raw_tools = _read_json_response(parent_sock, deadline)
    except (OSError, TimeoutError) as exc:
        return False, "private_tools_list_timeout", str(exc)

    if raw_tools is None or "result" not in raw_tools:
        return False, "private_tools_list_invalid_response", str(raw_tools)

    res_raw = raw_tools["result"]
    if not isinstance(res_raw, dict):
        return False, "private_tools_list_invalid_response", str(res_raw)

    tools_raw = cast("dict[str, object]", res_raw).get("tools")
    tools_list: list[dict[str, object]] = []
    if isinstance(tools_raw, list):
        tools_list.extend(
            cast("dict[str, object]", item) for item in tools_raw if isinstance(item, dict)
        )

    candidate_tool_hash = compute_tool_set_hash(tools_list)

    if expected_tool_hash is not None and candidate_tool_hash != expected_tool_hash:
        return (
            False,
            "tool_set_drift",
            f"schema hash mismatch: candidate={candidate_tool_hash} expected={expected_tool_hash}",
        )
    return True, None, candidate_tool_hash


def verify_candidate_readiness(
    parent_sock: socket.socket,
    *,
    expected_spawn_id: str,
    expected_pid: int,
    expected_session_id: str,
    expected_broker_fingerprint: str | None,
    expected_tool_hash: str | None = None,
    timeout: float = _PRIVATE_READINESS_TIMEOUT_S,
) -> tuple[bool, str | None, str | None]:
    """Verify the candidate over the private socketpair.

    Returns:
        (success, failure_stage, tool_hash_or_diff)
    """
    deadline = time.monotonic() + timeout
    parent_sock.settimeout(max(0.1, deadline - time.monotonic()))

    init_ok, init_stage, init_err = _verify_init_exchange(
        parent_sock,
        deadline,
        expected_pid=expected_pid,
        expected_spawn_id=expected_spawn_id,
        expected_session_id=expected_session_id,
        expected_broker_fingerprint=expected_broker_fingerprint,
    )
    if not init_ok:
        return False, init_stage, init_err

    return _verify_tools_exchange(parent_sock, deadline, expected_tool_hash=expected_tool_hash)


def _read_json_response(sock: socket.socket, deadline: float) -> dict[str, object] | None:
    buf = b""
    while time.monotonic() < deadline:
        sock.settimeout(max(0.05, deadline - time.monotonic()))
        try:
            chunk = sock.recv(4096)
        except (TimeoutError, BlockingIOError):
            continue
        if not chunk:
            break
        buf += chunk
        if b"\n" in buf:
            line, _ = buf.split(b"\n", 1)
            try:
                data: object = json.loads(line.decode("utf-8"))
                if isinstance(data, dict):
                    return cast("dict[str, object]", data)
            except (json.JSONDecodeError, UnicodeDecodeError):
                return None
    return None


def request_old_child_pause(pause_path: Path) -> None:
    """Signal the retiring child to stop accepting connections."""
    pause_path.parent.mkdir(parents=True, exist_ok=True)
    # filesystem-write-ok: transient handover sentinel file written once to request process pause
    pause_path.write_text("PAUSE\n", encoding="utf-8")


def wait_for_old_child_pause_ack(
    pause_path: Path,
    timeout: float = _HANDOVER_ACK_TIMEOUT_S,
    sleep_fn: Callable[[float], object] | None = None,
) -> bool:
    """Wait for the retiring child to acknowledge pause by dropping its socket."""
    ack_path = pause_path.with_name(pause_path.name + ".ack")
    deadline = time.monotonic() + timeout
    sleeper = sleep_fn if sleep_fn is not None else time.sleep
    while time.monotonic() < deadline:
        if ack_path.exists():
            return True
        sleeper(0.02)
    return ack_path.exists()


def signal_candidate_resume(resume_path: Path) -> None:
    """Signal the candidate child to start accepting connections."""
    resume_path.parent.mkdir(parents=True, exist_ok=True)
    # filesystem-write-ok: transient handover sentinel file written once to signal candidate resume
    resume_path.write_text("RESUME\n", encoding="utf-8")


def wait_for_candidate_resume(
    resume_path: Path,
    timeout: float = 30.0,
    stop_event: threading.Event | None = None,
) -> bool:
    """Wait for the candidate resume sentinel to appear before entering accept loop."""
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        if resume_path.exists():
            return True
        # filesystem-poll-ok: bounded poll interval awaiting candidate resume sentinel
        time.sleep(0.02)
    return resume_path.exists()


__all__ = [
    "_HANDOVER_ACK_TIMEOUT_S",
    "_HANDOVER_DRAIN_S",
    "_PRIVATE_READINESS_TIMEOUT_S",
    "compute_tool_set_hash",
    "create_handover_socketpair",
    "request_old_child_pause",
    "signal_candidate_resume",
    "start_private_readiness_listener",
    "verify_candidate_readiness",
    "wait_for_candidate_resume",
    "wait_for_old_child_pause_ack",
]

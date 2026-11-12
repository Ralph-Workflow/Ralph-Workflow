from __future__ import annotations

import hashlib
import json
import os
import socket
import threading
import time
import urllib.request
from dataclasses import dataclass
from typing import IO, TYPE_CHECKING, cast

from ralph.mcp.server._http_tools_list import _LIFECYCLE_MAX_RESPONSE_BYTES
from ralph.mcp.server._json_rpc_request import JsonRpcRequest

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

_HANDOVER_ACK_TIMEOUT_S: float = 1.0

_HANDOVER_DRAIN_S: float = 0.5

_PRIVATE_READINESS_TIMEOUT_S: float = 5.0

_CANDIDATE_BOOT_ALLOWANCE_S: float = 15.0

if not _HANDOVER_ACK_TIMEOUT_S > 0:
    raise RuntimeError(f"_HANDOVER_ACK_TIMEOUT_S must be positive (got {_HANDOVER_ACK_TIMEOUT_S})")

if not _HANDOVER_DRAIN_S > 0:
    raise RuntimeError(f"_HANDOVER_DRAIN_S must be positive (got {_HANDOVER_DRAIN_S})")

if not _CANDIDATE_BOOT_ALLOWANCE_S > 0:
    raise RuntimeError(
        f"_CANDIDATE_BOOT_ALLOWANCE_S must be positive (got {_CANDIDATE_BOOT_ALLOWANCE_S})"
    )

if not _PRIVATE_READINESS_TIMEOUT_S > 0:
    raise RuntimeError(
        f"_PRIVATE_READINESS_TIMEOUT_S must be positive (got {_PRIVATE_READINESS_TIMEOUT_S})"
    )


def _tool_sort_key(t: dict[str, object]) -> str:
    name = t.get("name")
    return str(name) if name is not None else ""


def compute_tool_set_hash(tools: list[dict[str, object]]) -> str:
    sorted_tools = sorted(tools, key=_tool_sort_key)
    encoded = json.dumps(sorted_tools, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(encoded).hexdigest()


def canonical_tool_entries(raw_tools: object) -> list[dict[str, object]]:
    if not isinstance(raw_tools, list):
        return []
    result: list[dict[str, object]] = []
    for item in raw_tools:
        if not isinstance(item, dict):
            continue
        entry = cast("dict[str, object]", item)
        schema = entry.get("inputSchema", {})
        result.append(
            {
                "name": str(entry.get("name", "")),
                "description": str(entry.get("description", "")),
                "inputSchema": schema if isinstance(schema, dict) else {},
            }
        )
    return result


def describe_tool_set_diff(
    expected: list[dict[str, object]] | None,
    candidate: list[dict[str, object]] | None,
) -> str:
    if expected is None or candidate is None:
        return (
            f"added=<unknown> removed=<unknown> changed=[] baseline_captured={expected is not None}"
        )
    expected_map = {str(entry.get("name", "")): entry for entry in expected}
    candidate_map = {str(entry.get("name", "")): entry for entry in candidate}
    added = sorted(name for name in candidate_map if name not in expected_map)
    removed = sorted(name for name in expected_map if name not in candidate_map)
    changed = sorted(
        name
        for name in expected_map.keys() & candidate_map.keys()
        if expected_map[name] != candidate_map[name]
    )
    return f"added={added} removed={removed} changed={changed}"


def served_tool_entries(endpoint: str, timeout: float) -> list[dict[str, object]]:
    payload: dict[str, object] = {
        "jsonrpc": "2.0",
        "method": "tools/list",
        "id": "1",
        "params": {},
    }
    request = urllib.request.Request(
        endpoint,
        data=json.dumps(payload).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    response = cast(
        "IO[bytes]",
        urllib.request.urlopen(request, timeout=timeout),
    )
    try:
        raw = response.read(_LIFECYCLE_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
    finally:
        response.close()
    if not raw:
        raise ValueError(f"empty tools/list response from {endpoint}")
    data_lines = [line[len("data: ") :] for line in raw.splitlines() if line.startswith("data: ")]
    try:
        decoded: object = json.loads(data_lines[0] if data_lines else raw)
    except json.JSONDecodeError as exc:
        raise ValueError(f"tools/list response from {endpoint} is not JSON") from exc
    if not isinstance(decoded, dict):
        raise ValueError(f"tools/list response from {endpoint} is not a JSON-RPC envelope")
    result = cast("dict[str, object]", decoded).get("result")
    if not isinstance(result, dict):
        raise ValueError(f"tools/list response from {endpoint} has no result object")
    raw_tools = cast("dict[str, object]", result).get("tools")
    if not isinstance(raw_tools, list):
        raise ValueError(f"tools/list response from {endpoint} has no tools array")
    return canonical_tool_entries(raw_tools)


def create_handover_socketpair() -> tuple[socket.socket, socket.socket]:
    parent_sock, child_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    parent_sock.settimeout(_PRIVATE_READINESS_TIMEOUT_S)
    return parent_sock, child_sock


def _resolve_raw_tools(mcp_server: object) -> list[object]:
    registry: object = getattr(mcp_server, "_registry", None)
    tools_attr: object = getattr(registry, "tools", None)
    if isinstance(tools_attr, list):
        return tools_attr
    list_defs: object = getattr(registry, "list_definitions", None)
    if callable(list_defs):
        try:
            defs: object = list_defs()
            if isinstance(defs, list):
                return defs
        except Exception:
            pass
    self_tools: object = getattr(mcp_server, "tools", None)
    if isinstance(self_tools, list):
        return self_tools
    return []


def _build_tools_list_response(mcp_server: object) -> list[dict[str, object]]:
    handle_tools: object = getattr(mcp_server, "_handle_tools_list", None)
    if callable(handle_tools):
        try:
            req = JsonRpcRequest(jsonrpc="2.0", method="tools/list", params={}, msg_id=1)
            raw_res: object = handle_tools(req)
            if isinstance(raw_res, tuple) and len(raw_res) >= 1:
                res_dict: object = getattr(raw_res[0], "result", None)
                if isinstance(res_dict, dict):
                    tools_val: object = res_dict.get("tools")
                    if isinstance(tools_val, list):
                        return canonical_tool_entries(cast("list[object]", tools_val))
        except Exception:
            pass

    raw_tools = _resolve_raw_tools(mcp_server)
    if not raw_tools:
        return []
    raw: list[dict[str, object]] = []
    for item in raw_tools:
        if isinstance(item, dict):
            raw.append(cast("dict[str, object]", item))
        else:
            name_obj: object = getattr(item, "name", "")
            desc_obj: object = getattr(item, "description", "")
            schema_obj: object = getattr(item, "input_schema", {})
            raw.append(
                {
                    "name": str(name_obj),
                    "description": str(desc_obj),
                    "inputSchema": schema_obj,
                }
            )
    return canonical_tool_entries(raw)


def _handle_readiness_line(
    line: bytes,
    mcp_server: object,
    *,
    stage_id: str,
    session_id: str,
    broker_fingerprint: str | None,
    effective_config_fingerprint: str | None,
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
                "effective_config_fingerprint": effective_config_fingerprint,
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
    mcp_server: object,
    *,
    stage_id: str,
    session_id: str,
    broker_fingerprint: str | None,
    effective_config_fingerprint: str | None = None,
) -> threading.Thread:

    def _worker() -> None:
        try:
            with child_sock:
                child_sock.settimeout(1.0)
                buf = b""
                while True:
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
                            effective_config_fingerprint=effective_config_fingerprint,
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
    expected_effective_config_fingerprint: str | None,
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
    cfg_fp_val = res.get("effective_config_fingerprint")
    if (
        expected_effective_config_fingerprint is not None
        and cfg_fp_val != expected_effective_config_fingerprint
    ):
        return (
            "effective_config_fingerprint_mismatch",
            f"got {cfg_fp_val} expected {expected_effective_config_fingerprint}",
        )
    return None


@dataclass(frozen=True)
class CandidateReadiness:
    ok: bool
    failure_stage: str | None = None
    detail: str | None = None
    tool_hash: str | None = None
    tools: tuple[dict[str, object], ...] = ()


def _failure(stage: str, detail: str) -> CandidateReadiness:
    return CandidateReadiness(ok=False, failure_stage=stage, detail=detail)


def _verify_init_exchange(
    parent_sock: socket.socket,
    deadline: float,
    *,
    expected_pid: int,
    expected_spawn_id: str,
    expected_session_id: str,
    expected_broker_fingerprint: str | None,
    expected_effective_config_fingerprint: str | None,
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
        expected_effective_config_fingerprint=expected_effective_config_fingerprint,
    )
    if meta_err is not None:
        return False, meta_err[0], meta_err[1]
    return True, None, None


def _verify_tools_exchange(
    parent_sock: socket.socket,
    deadline: float,
    *,
    expected_tool_hash: str | None,
    expected_tool_hash_required: bool = False,
) -> CandidateReadiness:
    tools_req: dict[str, object] = {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}}
    try:
        parent_sock.sendall(json.dumps(tools_req).encode("utf-8") + b"\n")
        raw_tools = _read_json_response(parent_sock, deadline)
    except (OSError, TimeoutError) as exc:
        return _failure("private_tools_list_timeout", str(exc))

    if raw_tools is None or "result" not in raw_tools:
        return _failure("private_tools_list_invalid_response", str(raw_tools))

    res_raw = raw_tools["result"]
    if not isinstance(res_raw, dict):
        return _failure("private_tools_list_invalid_response", str(res_raw))

    tools_list = canonical_tool_entries(cast("dict[str, object]", res_raw).get("tools"))
    candidate_tool_hash = compute_tool_set_hash(tools_list)
    entries = tuple(tools_list)

    if expected_tool_hash_required and expected_tool_hash is None:
        return CandidateReadiness(
            ok=False,
            failure_stage="baseline_unavailable",
            detail=(
                "no baseline tool-set hash was captured before candidate verification; "
                "refusing to promote a candidate without a drift reference"
            ),
            tool_hash=candidate_tool_hash,
            tools=entries,
        )
    if expected_tool_hash is not None and candidate_tool_hash != expected_tool_hash:
        return CandidateReadiness(
            ok=False,
            failure_stage="tool_set_drift",
            detail=(
                f"schema hash mismatch: candidate={candidate_tool_hash} "
                f"expected={expected_tool_hash}"
            ),
            tool_hash=candidate_tool_hash,
            tools=entries,
        )
    return CandidateReadiness(ok=True, tool_hash=candidate_tool_hash, tools=entries)


def check_candidate_readiness(
    parent_sock: socket.socket,
    *,
    expected_spawn_id: str,
    expected_pid: int,
    expected_session_id: str,
    expected_broker_fingerprint: str | None,
    expected_effective_config_fingerprint: str | None = None,
    expected_tool_hash: str | None = None,
    expected_tool_hash_required: bool = False,
    timeout: float = _PRIVATE_READINESS_TIMEOUT_S,
) -> CandidateReadiness:
    deadline = time.monotonic() + timeout
    parent_sock.settimeout(max(0.1, deadline - time.monotonic()))

    init_ok, init_stage, init_err = _verify_init_exchange(
        parent_sock,
        deadline,
        expected_pid=expected_pid,
        expected_spawn_id=expected_spawn_id,
        expected_session_id=expected_session_id,
        expected_broker_fingerprint=expected_broker_fingerprint,
        expected_effective_config_fingerprint=expected_effective_config_fingerprint,
    )
    if not init_ok:
        return _failure(
            init_stage if init_stage is not None else "private_preflight_failed",
            init_err if init_err is not None else "",
        )

    return _verify_tools_exchange(
        parent_sock,
        deadline,
        expected_tool_hash=expected_tool_hash,
        expected_tool_hash_required=expected_tool_hash_required,
    )


def verify_candidate_readiness(
    parent_sock: socket.socket,
    *,
    expected_spawn_id: str,
    expected_pid: int,
    expected_session_id: str,
    expected_broker_fingerprint: str | None,
    expected_effective_config_fingerprint: str | None = None,
    expected_tool_hash: str | None = None,
    timeout: float = _PRIVATE_READINESS_TIMEOUT_S,
) -> tuple[bool, str | None, str | None]:
    result = check_candidate_readiness(
        parent_sock,
        expected_spawn_id=expected_spawn_id,
        expected_pid=expected_pid,
        expected_session_id=expected_session_id,
        expected_broker_fingerprint=expected_broker_fingerprint,
        expected_effective_config_fingerprint=expected_effective_config_fingerprint,
        expected_tool_hash=expected_tool_hash,
        timeout=timeout,
    )
    detail = result.detail if not result.ok else result.tool_hash
    return result.ok, result.failure_stage, detail


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
    pause_path.parent.mkdir(parents=True, exist_ok=True)
    pause_path.write_text("PAUSE\n", encoding="utf-8")


def wait_for_old_child_pause_ack(
    pause_path: Path,
    timeout: float = _HANDOVER_ACK_TIMEOUT_S,
    sleep_fn: Callable[[float], object] | None = None,
) -> bool:
    ack_path = pause_path.with_name(pause_path.name + ".ack")
    deadline = time.monotonic() + timeout
    sleeper = sleep_fn if sleep_fn is not None else time.sleep
    while time.monotonic() < deadline:
        if ack_path.exists():
            return True
        sleeper(0.005)
    return ack_path.exists()


def signal_candidate_resume(resume_path: Path) -> None:
    resume_path.parent.mkdir(parents=True, exist_ok=True)
    resume_path.write_text("RESUME\n", encoding="utf-8")


def wait_for_candidate_resume(
    resume_path: Path,
    timeout: float = 30.0,
    stop_event: threading.Event | None = None,
) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if stop_event is not None and stop_event.is_set():
            return False
        if resume_path.exists():
            return True
        time.sleep(0.005)
    return resume_path.exists()


__all__ = [
    "_CANDIDATE_BOOT_ALLOWANCE_S",
    "_HANDOVER_ACK_TIMEOUT_S",
    "_HANDOVER_DRAIN_S",
    "_PRIVATE_READINESS_TIMEOUT_S",
    "CandidateReadiness",
    "canonical_tool_entries",
    "check_candidate_readiness",
    "compute_tool_set_hash",
    "create_handover_socketpair",
    "describe_tool_set_diff",
    "request_old_child_pause",
    "served_tool_entries",
    "signal_candidate_resume",
    "start_private_readiness_listener",
    "verify_candidate_readiness",
    "wait_for_candidate_resume",
    "wait_for_old_child_pause_ack",
]

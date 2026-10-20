"""Ordered handover protocol and private readiness channel for make-before-break restarts.

This module owns:
- Parent-owned AF_UNIX socketpair for private candidate preflight.
- Single-acceptor ordered handover via pause/ack/resume sentinels (FILES,
  not sockets: the retiring child acknowledges by creating
  ``<pause>.ack`` next to the pause sentinel).
- Tool-set hash verification to prevent silent tool drift (E1.9), including
  the canonical projection both channels hash and the parent-side served
  ``tools/list`` fetch that seeds the first baseline.
- Bounded handover and drain deadlines.
"""

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

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

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


def canonical_tool_entries(raw_tools: object) -> list[dict[str, object]]:
    """Project any served ``tools`` array onto the canonical drift-hash shape.

    Both sides of the comparison must hash the SAME bytes or drift detection
    is meaningless:

    * the parent captures its baseline from the shared endpoint's served
      ``tools/list`` (:func:`served_tool_entries`), and
    * the candidate is verified over the private readiness channel
      (:func:`check_candidate_readiness`).

    Each entry collapses to exactly ``{name, description, inputSchema}`` with
    string name/description and a dict schema. Extra keys a server may add
    (annotations, titles, vendor hints) are dropped because they are not part
    of the E1.9 contract; missing fields take the same defaults
    :func:`_build_tools_list_response` applies, so the projection is
    idempotent on already-canonical input.
    """
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
    """Describe drift by TOOL NAME so a refusal log names what actually changed.

    Hash equality only says *that* the catalogues differ; an operator reading
    the WARNING needs to know which tools were added, removed, or re-described.
    A missing baseline (``None``) is reported explicitly rather than rendered
    as "everything was added".
    """
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
    """Fetch the SERVED ``tools/list`` over HTTP and canonicalise it for hashing.

    This is the parent-side baseline source. It deliberately reads the wire
    response rather than a parent-side catalogue snapshot: only the served list
    is what an agent actually sees, so it is the only sound drift reference.

    Bounded on both axes: the socket/response wait is capped by ``timeout`` and
    the body read by ``_LIFECYCLE_MAX_RESPONSE_BYTES``, so a wedged or
    misbehaving child cannot stall or OOM the parent. Both the plain JSON body
    and the SSE ``data:`` envelope used by the fallback server are accepted.

    Raises:
        OSError: transport failure or a body that is not a JSON-RPC envelope.
        ValueError: the response is not decodable as a ``tools/list`` result.
    """
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
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    try:
        raw = response.read(_LIFECYCLE_MAX_RESPONSE_BYTES).decode("utf-8", errors="replace")
    finally:
        response.close()
    if not raw:
        raise ValueError(f"empty tools/list response from {endpoint}")
    # The fallback standalone server answers with an SSE frame
    # ``event: message\\ndata: {json}\\n\\n``; unwrap it when present.
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
    """Create a parent/child connected AF_UNIX stream socketpair."""
    parent_sock, child_sock = socket.socketpair(socket.AF_UNIX, socket.SOCK_STREAM)
    parent_sock.settimeout(_PRIVATE_READINESS_TIMEOUT_S)
    return parent_sock, child_sock


def _build_tools_list_response(mcp_server: object) -> list[dict[str, object]]:
    registry: object = getattr(mcp_server, "_registry", None)
    tools_attr: object = getattr(registry, "tools", None)
    if not isinstance(tools_attr, list):
        return []
    raw: list[dict[str, object]] = []
    for item in tools_attr:
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
    # Same projection the parent hashes its served baseline through, so the two
    # channels are byte-comparable by construction rather than by convention.
    return canonical_tool_entries(raw)


def _handle_readiness_line(
    line: bytes,
    mcp_server: object,
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
    mcp_server: object,
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


@dataclass(frozen=True)
class CandidateReadiness:
    """Typed outcome of one private-channel candidate preflight.

    ``detail`` is the legacy human-readable third element (an error string on
    failure, ``None`` on success). ``tools`` and ``tool_hash`` carry the
    candidate's CANONICAL served catalogue and its hash on BOTH outcomes, so a
    caller can retain the validated hash (or render a name-level drift diff)
    without re-parsing ``detail``.
    """

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

    # Canonicalise exactly as the parent's served-endpoint baseline does, so a
    # candidate hash and a baseline hash are comparable byte-for-byte.
    tools_list = canonical_tool_entries(cast("dict[str, object]", res_raw).get("tools"))
    candidate_tool_hash = compute_tool_set_hash(tools_list)
    entries = tuple(tools_list)

    # C1/E1.9 fail-closed gate: when the bridge never captured a baseline
    # (capture timed out / returned empty / errored) it has no drift reference.
    # Without this gate the comparison below would be silently skipped and any
    # candidate — drifted or not — would replace the living child. The bridge
    # now reports ``baseline_unavailable`` so the caller can apply the only
    # legal escape (dead-old-child promotion).
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
    expected_tool_hash: str | None = None,
    expected_tool_hash_required: bool = False,
    timeout: float = _PRIVATE_READINESS_TIMEOUT_S,
) -> CandidateReadiness:
    """Verify the candidate over the private socketpair and return the typed outcome.

    The candidate's canonical served catalogue and its hash are returned on both
    success and drift so the caller can retain the validated hash or render a
    name-level drift diff without parsing the human-readable ``detail``.

    ``expected_tool_hash_required`` is the C1/E1.9 fail-closed switch: when
    ``True`` and ``expected_tool_hash`` is ``None``, the tools exchange refuses
    with ``failure_stage="baseline_unavailable"``. The caller (the bridge)
    consults the old child's liveness and either refuses the promotion or
    applies the only legal escape (the dead-old-child exception).
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
    expected_tool_hash: str | None = None,
    timeout: float = _PRIVATE_READINESS_TIMEOUT_S,
) -> tuple[bool, str | None, str | None]:
    """Tuple view of :func:`check_candidate_readiness`.

    Returns ``(success, failure_stage, tool_hash_or_diff)``. Prefer
    :func:`check_candidate_readiness` where the candidate's catalogue or hash
    is needed; this wrapper exists for callers that only need the verdict.
    """
    result = check_candidate_readiness(
        parent_sock,
        expected_spawn_id=expected_spawn_id,
        expected_pid=expected_pid,
        expected_session_id=expected_session_id,
        expected_broker_fingerprint=expected_broker_fingerprint,
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
    """Signal the retiring child to stop accepting connections."""
    pause_path.parent.mkdir(parents=True, exist_ok=True)
    # filesystem-write-ok: transient handover sentinel file written once to request process pause
    pause_path.write_text("PAUSE\n", encoding="utf-8")


def wait_for_old_child_pause_ack(
    pause_path: Path,
    timeout: float = _HANDOVER_ACK_TIMEOUT_S,
    sleep_fn: Callable[[float], object] | None = None,
) -> bool:
    """Wait for the retiring child to acknowledge pause with an ack FILE.

    The acknowledgement is a sentinel file (``<pause>.ack``) created by the old
    child once it has dropped its accepting FD — NOT a message on the handover
    socketpair, which carries only the private preflight exchange. Waiting on
    the socket here would hang until the ack timeout on every healthy handover.
    """
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

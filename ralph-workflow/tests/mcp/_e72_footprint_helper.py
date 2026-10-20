"""Helper for the E7.2 make-before-break footprint measurement test.

Spawns a REAL MCP serving child via :func:`get_process_manager().spawn`
rather than a sleeping ``python -c``. The child:

* adopts the bridge-owned listening FD (via ``RALPH_MCP_INHERITED_FD``) and
  serves JSON-RPC ``tools/list`` over HTTP at the shared endpoint so the
  bridge's baseline capture succeeds on the produced (real) HTTP wire;
* adopts the private socketpair child end (via
  ``RALPH_MCP_BRIDGE_PARENT_SOCK``) and answers ``initialize`` and
  ``tools/list`` for the bridge's private preflight handshake;
* watches ``RALPH_MCP_ACCEPT_PAUSE_FILE`` and writes ``.ack`` then exits
  when the bridge signals pause during make-before-break handover;
* waits on ``RALPH_MCP_ACCEPT_RESUME_FILE`` before fully entering the
  serve-forever loop when the bridge launches it as a candidate.

The HTTP responder and the private-readiness responder return the SAME
``TOOLS`` list (passed via argv) so the served-endpoint baseline hash and
the private-channel ``tools/list`` hash match exactly — that is what
makes :class:`lifecycle.RestartAwareMcpBridge.capture_tool_hash_baseline`
and :func:`check_candidate_readiness` byte-comparable end-to-end.

This helper reuses the "real subprocess + minimal MCP-shaped handlers"
approach that :mod:`tests.mcp.test_root_cause_real_child_bridge` and
:mod:`tests.mcp.test_handover_inflight_real_child` already employ; no
parallel fixture is introduced — only the smallest additional surface
needed to wire HTTP serving on the inherited FD and pause/resume
sentinels around it.
"""

from __future__ import annotations

import contextlib
import json
import socket
import sys
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.mcp.protocol.env import (
    MCP_ACCEPT_PAUSE_FILE,
    MCP_ACCEPT_RESUME_FILE,
    MCP_BRIDGE_PARENT_SOCK,
    MCP_INHERITED_FD_ENV,
    MCP_STAGE_ID_ENV,
)
from ralph.mcp.server._handover_protocol import create_handover_socketpair
from ralph.mcp.server._snapshots import snapshot_dir
from ralph.process.manager import SpawnOptions, get_process_manager

# ``MCP_SESSION_ID_ENV`` is named in ``ralph.mcp.server.lifecycle`` rather than
# ``ralph.mcp.protocol.env`` (see lifecycle.MCP_SESSION_ID_ENV). The child
# script needs the same value so the private preflight's ``session_id`` check
# matches the bridge's expectation.
MCP_SESSION_ID_ENV = "MCP_SESSION_ID"

if TYPE_CHECKING:
    from ralph.process.manager import ManagedProcess


_CHILD_SCRIPT = r"""import json, os, socket, sys, threading, time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path


_TOOLS = json.loads(sys.argv[1])


def _respond(req):
    method = req.get("method")
    if method == "initialize":
        return {
            "jsonrpc": "2.0",
            "id": req.get("id"),
            "result": {
                "protocolVersion": "2024-11-05",
                "serverInfo": {"name": "ralph-mcp", "version": "0.1.0"},
                "pid": os.getpid(),
                "spawn_id": os.environ.get("RALPH_MCP_STAGE_ID", ""),
                "session_id": os.environ.get("RALPH_MCP_SESSION_ID", ""),
                "broker_secret_fingerprint": None,
            },
        }
    if method == "tools/list":
        return {"jsonrpc": "2.0", "id": req.get("id"), "result": {"tools": _TOOLS}}
    return {"jsonrpc": "2.0", "id": req.get("id"), "result": {}}


def _readiness_loop(fd):
    sock = socket.socket(fileno=fd)
    sock.settimeout(15.0)
    buf = b""
    while True:
        try:
            chunk = sock.recv(4096)
        except (TimeoutError, OSError):
            break
        if not chunk:
            break
        buf += chunk
        while b"\n" in buf:
            line, buf = buf.split(b"\n", 1)
            if not line.strip():
                continue
            try:
                req = json.loads(line.decode("utf-8"))
            except Exception:
                continue
            try:
                sock.sendall(json.dumps(_respond(req)).encode("utf-8") + b"\n")
            except (BrokenPipeError, OSError):
                return


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def do_POST(self):
        length = int(self.headers.get("Content-Length", "0"))
        if length:
            self.rfile.read(length)
        body = json.dumps(
            {"jsonrpc": "2.0", "id": 1, "result": {"tools": _TOOLS}}
        ).encode("utf-8")
        self.send_response(200)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(body)
            self.wfile.flush()
        except (BrokenPipeError, OSError):
            pass

    def do_GET(self):
        if self.path == "/health":
            self.send_response(200)
            self.send_header("Content-Length", "2")
            self.send_header("Connection", "close")
            self.end_headers()
            try:
                self.wfile.write(b"OK")
                self.wfile.flush()
            except (BrokenPipeError, OSError):
                pass
            return
        self.send_response(200)
        self.send_header("Content-Type", "text/event-stream")
        self.send_header("Content-Length", "16")
        self.send_header("Connection", "close")
        self.end_headers()
        try:
            self.wfile.write(b"event: open\r\ndata: {}\r\n\r\n")
            self.wfile.flush()
        except (BrokenPipeError, OSError):
            pass

    def log_message(self, *args, **kwargs):
        return


def _serve_http(listen_fd, ready_event):
    sock = socket.socket(fileno=listen_fd)
    httpd = ThreadingHTTPServer(
        ("127.0.0.1", 0), _Handler, bind_and_activate=False
    )
    httpd.socket = sock
    httpd.server_address = ("127.0.0.1", sock.getsockname()[1])
    if ready_event is not None:
        ready_event.set()
    httpd.serve_forever(poll_interval=0.01)


def _pause_watcher(pause_path):
    p = Path(pause_path)
    while True:
        if p.exists():
            ack = p.with_name(p.name + ".ack")
            try:
                ack.write_text("ACK\n", encoding="utf-8")
            except Exception:
                pass
            os._exit(0)
        time.sleep(0.005)


_inherited = int(os.environ.get("RALPH_MCP_INHERITED_FD", "-1"))
_psock_fd = int(os.environ.get("RALPH_MCP_BRIDGE_PARENT_SOCK", "-1") or "-1")
_pause_path = os.environ.get("RALPH_MCP_ACCEPT_PAUSE_FILE") or ""
_resume_path = os.environ.get("RALPH_MCP_ACCEPT_RESUME_FILE") or ""

if _inherited < 0:
    sys.stderr.write("missing RALPH_MCP_INHERITED_FD env\n")
    sys.exit(2)

http_ready = threading.Event()
threading.Thread(target=_serve_http, args=(_inherited, http_ready), daemon=True).start()
http_ready.wait(timeout=10.0)

if _psock_fd >= 0:
    threading.Thread(target=_readiness_loop, args=(_psock_fd,), daemon=True).start()

if _resume_path:
    p = Path(_resume_path)
    deadline = time.monotonic() + 30.0
    while time.monotonic() < deadline:
        if p.exists():
            break
        time.sleep(0.005)

if _pause_path:
    threading.Thread(target=_pause_watcher, args=(_pause_path,), daemon=True).start()

# Hold the inherited fd open until the bridge's shutdown/pause signal
# terminates the child. ``Event.wait`` without a timeout is bounded by the
# bridge-driven process lifecycle, not by wall-clock.
threading.Event().wait()
"""


def _write_child_script(tmp_path: Path) -> Path:
    script = tmp_path / "e72_mcp_child.py"
    script.write_text(_CHILD_SCRIPT, encoding="utf-8")
    return script


def spawn_serving_child(
    tmp_path: Path,
    *,
    sock_fd: int,
    stage_id: str,
    session_id: str,
    make_before_break: bool,
    tools: list[dict[str, object]],
    label: str,
    snap_base: Path,
    run_id: str,
) -> tuple[ManagedProcess, socket.socket | None]:
    """Spawn a real MCP serving child that adopts the bridge-owned listening fd.

    When ``make_before_break`` is ``True``, also wires the private
    handover socketpair so the bridge can run its full private preflight
    against the candidate. Returns the spawned process and the parent
    end of the handover socketpair (or ``None`` when not make-before-break).

    ``snap_base`` and ``run_id`` are used to compute the snapshot directory
    with the same factory the bridge uses (``snapshot_dir`` from
    :mod:`ralph.mcp.server._snapshots`), so pause/resume sentinel files
    written by either side land at the same absolute path.

    The OLD child (``make_before_break=False``) still receives
    ``MCP_ACCEPT_PAUSE_FILE`` so the bridge's pause/ack handshake finds it
    and can retire the child cleanly without the kill-on-hang escape
    escalating inside the bounded handover window.
    """
    pm = get_process_manager()
    script = _write_child_script(tmp_path)

    snap_dir = snapshot_dir(snap_base, run_id)
    pause_f = snap_dir / f"accept.pause.{stage_id}"

    env: dict[str, str] = {
        MCP_INHERITED_FD_ENV: str(sock_fd),
        MCP_STAGE_ID_ENV: stage_id,
        MCP_SESSION_ID_ENV: session_id,
        MCP_ACCEPT_PAUSE_FILE: str(pause_f),
    }
    pass_fds: tuple[int, ...] = (sock_fd,)
    parent_sock: socket.socket | None = None
    child_sock: socket.socket | None = None

    if make_before_break:
        resume_f = snap_dir / f"accept.resume.{stage_id}"
        env[MCP_ACCEPT_RESUME_FILE] = str(resume_f)
        parent_sock, child_sock = create_handover_socketpair()
        child_fd = child_sock.fileno()
        env[MCP_BRIDGE_PARENT_SOCK] = str(child_fd)
        pass_fds = (*pass_fds, child_fd)

    try:
        proc = pm.spawn(
            [sys.executable, str(script), json.dumps(tools)],
            SpawnOptions(
                cwd=str(tmp_path),
                env=env,
                pass_fds=pass_fds,
                label=label,
            ),
        )
    finally:
        if child_sock is not None:
            with contextlib.suppress(Exception):
                child_sock.close()

    return proc, parent_sock


__all__ = ["spawn_serving_child"]

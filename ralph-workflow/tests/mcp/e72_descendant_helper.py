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
from tests.mcp._e72_footprint_helper import _CHILD_SCRIPT as _MCP_SERVING_CHILD_SCRIPT

if TYPE_CHECKING:
    from ralph.process.manager import ManagedProcess

MCP_SESSION_ID_ENV = "MCP_SESSION_ID"

_UPSTREAM_SCRIPT_ENV = "RALPH_E72_UPSTREAM_SCRIPT"
_UPSTREAM_PIDFILE_ENV = "RALPH_E72_UPSTREAM_PIDFILE"
_MCP_CHILD_SCRIPT_ENV = "RALPH_E72_MCP_CHILD_SCRIPT"

_UPSTREAM_WATCHDOG_S = 8.0


_LAUNCHER_SCRIPT = r'''"""Create the upstream descendant, then become the MCP serving child."""
import os
import subprocess
import sys
from pathlib import Path


def _spawn_upstream(script_path, pid_file_path):
    """Start the upstream descendant process and publish its pid.

    ``close_fds`` stays True and only the liveness pipe read end crosses the
    boundary, so the upstream never inherits the bridge-owned listening socket
    nor the private handover socket. That separation is what makes the
    listening-fd-holder count an independent measurement of the tree.

    The pipe write end stays open here and is marked inheritable so it
    survives the ``execv`` below into the MCP serving child: the upstream then
    sees EOF exactly when that process dies, rather than immediately.
    """
    read_fd, write_fd = os.pipe()
    upstream = subprocess.Popen(
        [sys.executable, script_path, str(read_fd)],
        pass_fds=(read_fd,),
        close_fds=True,
    )
    os.close(read_fd)
    os.set_inheritable(write_fd, True)
    Path(pid_file_path).write_text(str(upstream.pid), encoding="utf-8")
    return upstream.pid


_spawn_upstream(
    os.environ["RALPH_E72_UPSTREAM_SCRIPT"],
    os.environ["RALPH_E72_UPSTREAM_PIDFILE"],
)
os.execv(
    sys.executable,
    [sys.executable, os.environ["RALPH_E72_MCP_CHILD_SCRIPT"], sys.argv[1]],
)
'''


_UPSTREAM_DESCENDANT_SCRIPT = r'''"""Upstream MCP server stand-in: a live descendant of the MCP serving child."""
import os
import select
import sys
import time

# The liveness pipe read end is the only fd this process received: it holds
# neither the bridge-owned listening socket nor the private handover socket.
# Nothing is ever written to the pipe, so ``select`` reports it readable
# exactly once the parent is gone and the kernel has closed its write end —
# which happens even when the parent leaves through ``os._exit``.
_liveness_fd = int(sys.argv[1])
_deadline = time.monotonic() + float(os.environ.get("RALPH_E72_UPSTREAM_WATCHDOG_S", "8"))

while time.monotonic() < _deadline:
    if select.select([_liveness_fd], [], [], 0.25)[0]:
        break
    time.sleep(0.02)

os._exit(0)
'''


def upstream_pidfile(tmp_path: Path, *, stage_id: str) -> Path:
    return tmp_path / f"upstream-{stage_id}.pid"


def read_recorded_upstream_pid(pid_file: Path) -> int | None:
    try:
        recorded = pid_file.read_text(encoding="utf-8").strip()
    except OSError:
        return None
    return int(recorded) if recorded.isdigit() else None


def spawn_serving_child_with_upstream(
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
    pm = get_process_manager()
    upstream_script = tmp_path / f"e72_upstream_{stage_id}.py"
    upstream_script.write_text(_UPSTREAM_DESCENDANT_SCRIPT, encoding="utf-8")
    child_script = tmp_path / f"e72_mcp_child_{stage_id}.py"
    child_script.write_text(_MCP_SERVING_CHILD_SCRIPT, encoding="utf-8")
    launcher_script = tmp_path / f"e72_tree_launcher_{stage_id}.py"
    launcher_script.write_text(_LAUNCHER_SCRIPT, encoding="utf-8")

    snap_dir = snapshot_dir(snap_base, run_id)
    pause_f = snap_dir / f"accept.pause.{stage_id}"

    env: dict[str, str] = {
        MCP_INHERITED_FD_ENV: str(sock_fd),
        MCP_STAGE_ID_ENV: stage_id,
        MCP_SESSION_ID_ENV: session_id,
        MCP_ACCEPT_PAUSE_FILE: str(pause_f),
        _UPSTREAM_SCRIPT_ENV: str(upstream_script),
        _UPSTREAM_PIDFILE_ENV: str(upstream_pidfile(tmp_path, stage_id=stage_id)),
        _MCP_CHILD_SCRIPT_ENV: str(child_script),
        "RALPH_E72_UPSTREAM_WATCHDOG_S": str(_UPSTREAM_WATCHDOG_S),
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
            [sys.executable, str(launcher_script), json.dumps(tools)],
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


__all__ = [
    "read_recorded_upstream_pid",
    "spawn_serving_child_with_upstream",
    "upstream_pidfile",
]

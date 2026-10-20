"""Helper giving the E7.2 footprint test a REAL descendant-bearing MCP child tree.

The make-before-break footprint that acceptance requirement E7.2 asks to
measure is the cost of *a whole bridge tree*, not the cost of one process. A
production MCP child is not a leaf: it owns the reserved listening socket,
its private handover socketpair, its session file, and one or more upstream
MCP server processes it proxied tools from. Replacing it therefore duplicates
the child's own footprint AND every descendant hanging off it. Measuring only
the direct child (as the previous revision of the test did) understates the
temporary doubling by exactly the descendant set.

This module adds the missing upstream layer WITHOUT duplicating the
production-shaped serving child: it reuses the child script from
:mod:`tests.mcp._e72_footprint_helper` verbatim (same HTTP responder, same
private readiness responder, same pause/resume sentinels, same
``tools/list`` catalogue so the bridge's tool-hash baseline still matches)
and spawns it behind a one-line launcher that first creates the upstream
descendant.

Process shape produced per bridge child::

    test process
      └── bridge child (real MCP serving child, owns the listening fd)
            └── upstream descendant (real process, holds NO listening fd)

The launcher creates the upstream descendant and then ``execv``s the
production child script, so the *same* pid serves as both the launcher and
the bridge child while the upstream stays its child for the whole overlap
window. That matters because the bridge retires the old child through
``os.killpg(pgid, SIGKILL)`` (the ProcessManager orphan sweep), which only
reaches the upstream because the child was spawned with
``start_new_session=True`` and the upstream inherits that process group.

Two properties are deliberately built into the upstream so the measurement
stays discriminating rather than merely additive:

* it never holds the bridge-owned listening fd — the liveness pipe read end
  is the only fd passed across — so the listening-fd holder count tracks
  bridge children only and proves the upstream is a real, separately
  accounted process instead of a duplicate of the process count;
* it exits when its parent's liveness pipe reports EOF (parent gone, including
  the ``os._exit(0)`` pause path that skips interpreter teardown), so the
  retirement leg of the footprint has a deterministic, bounded descendant
  lifecycle instead of an orphan that leaks into the rest of the suite.

POSIX only: the liveness pipe uses ``pass_fds`` and the ProcessManager
orphan sweep uses ``os.killpg``.
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
from tests.mcp._e72_footprint_helper import _CHILD_SCRIPT as _MCP_SERVING_CHILD_SCRIPT

if TYPE_CHECKING:
    from ralph.process.manager import ManagedProcess

# ``MCP_SESSION_ID_ENV`` is named in ``ralph.mcp.server.lifecycle`` rather than
# ``ralph.mcp.protocol.env`` (see lifecycle.MCP_SESSION_ID_ENV). The child
# script needs the same value so the private preflight's ``session_id`` check
# matches the bridge's expectation.
MCP_SESSION_ID_ENV = "MCP_SESSION_ID"

#: Environment variables the launcher passes to the upstream descendant script.
_UPSTREAM_SCRIPT_ENV = "RALPH_E72_UPSTREAM_SCRIPT"
_UPSTREAM_PIDFILE_ENV = "RALPH_E72_UPSTREAM_PIDFILE"
_MCP_CHILD_SCRIPT_ENV = "RALPH_E72_MCP_CHILD_SCRIPT"

#: Hard ceiling on how long the upstream descendant stays alive. The overlap
#: window is bounded by ``_HANDOVER_DRAIN_S`` (0.5 s) and the test itself is
#: capped at 8 s, so 8 s of self-sufficiency can never shorten a live
#: measurement while still guaranteeing no descendant outlives the test.
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
    """Return the path the MCP child publishes its upstream descendant pid to."""
    return tmp_path / f"upstream-{stage_id}.pid"


def read_recorded_upstream_pid(pid_file: Path) -> int | None:
    """Return the upstream pid the child recorded, or ``None`` when not yet written.

    Non-blocking by design: the concurrent footprint sampler calls this on
    every tick and the blocking phase assertions poll it from the test, so
    this helper owns no wall-clock of its own.
    """
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
    """Spawn a real MCP serving child that owns one real upstream descendant.

    The spawned process is the production-shaped serving child from
    :mod:`tests.mcp._e72_footprint_helper` (identical HTTP + private-readiness
    responders, identical ``tools`` catalogue, identical pause/resume
    sentinel wiring) reached through a launcher that creates the upstream
    descendant first. ``make_before_break=True`` also wires the private
    handover socketpair so the bridge can run its full preflight against the
    candidate.

    Returns the managed process and the parent end of the handover socketpair
    (``None`` when ``make_before_break`` is False), matching
    :func:`tests.mcp._e72_footprint_helper.spawn_serving_child`.
    """
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

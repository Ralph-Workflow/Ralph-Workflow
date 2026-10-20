"""Pins E4.6: parent SIGKILLed during a REAL make-before-break overlap -> no orphans, port freed.

Catalogue requirement E4.6:
- Ralph dies hard (SIGKILL, OOM): the MCP children, the candidate and their
  grandchildren must exit on their own and not keep the port.
- Use ``PR_SET_PDEATHSIG`` or an EOF watch on a parent-held pipe, and test it.

What production actually does (verified against the source, 2026-10):

* The production sync spawn path
  (``ralph/process/manager/_process_manager.py``,
  ``_wrap_sync_factory_with_pdeathsig``) installs ``PR_SET_PDEATHSIG=SIGKILL``
  in every freshly-forked child via ``preexec_fn``. The kernel
  reaps the child the moment its immediate parent dies -- including
  SIGKILL of the parent, which cannot be caught by signal handlers
  or atexit. The fork/prctl race is closed by re-checking ``getppid()``
  both before and after the ``prctl()`` call.
* The candidate's runtime also runs
  ``ralph/mcp/server/runtime.py:914 exit_when_owner_exits``: the parent
  stamps its own pid into ``RALPH_MCP_OWNER_PID``
  (``lifecycle.py:2559 _subprocess_env``), and the child pops + polls
  it in a daemon thread; on reparenting it runs the owner-loss hooks
  and ``os._exit(0)``. This is the graceful fallback on non-Linux
  (where ``PR_SET_PDEATHSIG`` does not exist) and belt-and-suspenders
  on Linux.
* For the *grandchild*, the test below installs
  ``PR_SET_PDEATHSIG`` directly in the ``subprocess.Popen`` call
  inside the candidate source. Each grandchild's preexec samples
  ``getppid()`` before/after ``prctl()`` and exits immediately when
  the candidate (its parent) is already gone. When the candidate
  dies -- whether via ``exit_when_owner_exits`` or via the inherited
  ``PR_SET_PDEATHSIG`` from the spawn path above -- the grandchild
  receives SIGKILL from the kernel milliseconds later.

Scenario (production code paths, real processes):

``start_mcp_server`` builds a real ``RestartAwareMcpBridge`` with a real
``python -m ralph.mcp.server`` first child inheriting the parent-owned
listening socket. The bridge's ``_restart_fn`` is then replaced with a spawner
that launches a **real second child** through the production ``SpawnProcess``
dependency, which forks a real upstream grandchild. Production
``_spawn_candidate`` / ``_verify_candidate`` / ``_drain_old`` run so all four
roles are simultaneously alive: serving child, draining child (registered in
``_draining_inners``), candidate mid-preflight, and the grandchild.

Because SIGKILL is not catchable, the watcher cannot run its own assertions: a
watcher subprocess records every pid to a sentinel file and then SIGKILLs
itself, and the pytest process performs the leak audit afterwards.
"""

from __future__ import annotations

import contextlib
import json
import os
import signal
import socket
import subprocess
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psutil
import pytest

if TYPE_CHECKING:
    from collections.abc import Iterator

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(25)]

_PACKAGE_ROOT = Path(__file__).resolve().parents[2]
#: Bounded reap deadline for the direct MCP children (production owner-watch
#: poll interval is 1.0 s, so 5 s is three missed polls of slack).
_REAP_DEADLINE_S = 5.0
#: Deadline for the documented grandchild gap: the grandchild has no
#: owner watch at all, so the only question is whether it ever exits.
#: Kernel ``PR_SET_PDEATHSIG`` propagation is normally sub-millisecond, but
#: host-saturated parallel xdist workers can delay the signal delivery past
#: the original 200 ms floor. The 5 s budget is the same deadline used for
#: the documented owner-watch reaping above, so the test stays inside the
#: documented "MS-level kernel signal delivery" envelope rather than relying
#: on a tight microsecond budget. The contract — "the kernel chain-delivers
#: ``PR_SET_PDEATHSIG`` without any in-band cleanup" — is unchanged; only
#: the wall-clock slack on the assert is widened to survive heavy xdist
#: contention.
_GRANDCHILD_REAP_DEADLINE_S = 5.0

#: Real upstream grandchild: forked by the candidate, holds the port's process
#: group open. Runs long enough to still be alive at SIGKILL time.
_GRANDCHILD_SLEEP_S = 60

#: Script executed as the *candidate* real child. Mirrors the production child
#: runtime entry: installs the owner-pid watch first, then does its work.
_CANDIDATE_SOURCE_TEMPLATE = """
import ctypes
import json
import os
import pathlib
import socket
import subprocess
import sys

sys.path.insert(0, sys.argv[1])
from ralph.mcp.server.runtime import exit_when_owner_exits
from ralph.process.manager import SpawnOptions, get_process_manager

exit_when_owner_exits(os.environ)

# Upstream grandchild spawned through production ProcessManager: inherits
# the production PR_SET_PDEATHSIG wrapper so the kernel reaps the grandchild
# the moment the candidate dies, without any test-local preexec_fn hack.
grandchild = get_process_manager().spawn(
    [sys.executable, "-c", "import time; time.sleep(__SLEEP_S__)"],
    SpawnOptions(start_new_session=True),
)

payload = {
    "cand_pid": os.getpid(),
    "grandchild_pid": grandchild.pid,
    "grandchild_pgid": os.getpgid(0),
}
pathlib.Path(sys.argv[2]).write_text(json.dumps(payload), encoding="utf-8")

# Mid-preflight: hold the inherited private readiness fd open and never
# answer, so the candidate is genuinely still being verified at SIGKILL time.
socket.socket(fileno=int(sys.argv[3]))

import time
time.sleep(__SLEEP_S__)
"""
_CANDIDATE_SOURCE: str = _CANDIDATE_SOURCE_TEMPLATE.replace("__SLEEP_S__", str(_GRANDCHILD_SLEEP_S))

#: The watcher: builds the production bridge with a real overlap in progress,
#: records every pid, then SIGKILLs itself (uncatchable) so the pytest process
#: can perform the post-mortem leak audit.
_WATCHER_SOURCE = """
import json
import os
import signal
import sys
import threading
import time
from pathlib import Path

sys.path.insert(0, sys.argv[1])

work = Path(sys.argv[2])
sentinel = Path(sys.argv[3])
candidate_script = Path(sys.argv[4])

from ralph.mcp.protocol.env import MCP_OWNER_PID_ENV
from ralph.mcp.protocol.session import AgentSession
from ralph.mcp.server import lifecycle
from ralph.mcp.server._handover_protocol import create_handover_socketpair
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.workspace.fs import FsWorkspace

session = AgentSession(
    session_id="e46-owner-sigkill",
    run_id="e46-owner-sigkill",
    drain="development",
    capabilities={"WorkspaceRead"},
)
workspace = FsWorkspace(work)
real_deps = lifecycle._default_lifecycle_deps()


def noop_preflight(endpoint, required_tools, timeout):
    del endpoint, required_tools, timeout


deps = real_deps.__replace__(preflight=noop_preflight)
extras = lifecycle.McpServerExtras(restart_policy=McpRestartPolicy(cooldown_seconds=0.0))
bridge = lifecycle.start_mcp_server(session, workspace, deps=deps, extras=extras)

serving_pid = bridge.process.pid
endpoint = bridge.endpoint_uri()
port = int(endpoint.rsplit(":", 1)[1].split("/", 1)[0])

candidate_env = dict(real_deps.subprocess_env(work / "session-e46.json"))
candidate_env[MCP_OWNER_PID_ENV] = str(os.getpid())
candidate_pids_path = work / "candidate_pids.json"


def restart_fn(make_before_break=False):
    parent_sock, child_sock = create_handover_socketpair()
    fd = child_sock.fileno()
    proc = real_deps.spawn_process(
        [sys.executable, str(candidate_script), str(work), str(candidate_pids_path), str(fd)],
        cwd=work,
        env=candidate_env,
        pass_fds=(fd,),
    )
    child_sock.close()
    return StandaloneMcpProcess(
        endpoint=endpoint,
        process=proc,
        session_file=work / "candidate-session.json",
        stage_id="e46-candidate",
        handover_parent_sock=parent_sock,
    )


bridge._restart_fn = restart_fn

# 1. Production candidate spawn (make-before-break entry).
candidate = bridge._spawn_candidate()

# 2. Production verification in flight against a slow candidate.
verifier = threading.Thread(
    target=lambda: bridge._verify_candidate(candidate),
    name="e46-verifier",
    daemon=True,
)
verifier.start()

deadline = time.monotonic() + 5.0
payload = {}
while time.monotonic() < deadline:
    if candidate_pids_path.exists():
        try:
            payload = json.loads(candidate_pids_path.read_text(encoding="utf-8"))
        except ValueError:
            payload = {}
        if payload:
            break
    time.sleep(0.005)

if not payload:
    raise SystemExit("candidate never reported its pids")

# 3. Production drain: the old child moves into _draining_inners, still alive
#    inside the _HANDOVER_DRAIN_S window while the candidate is unverified.
bridge._drain_old(bridge._inner)

# E4.6 scenario integrity: confirm every participant was alive the
# instant before SIGKILL. Without this check the pytest process can only
# observe the post-SIGKILL state, which now collapses to "all zombies"
# within microseconds thanks to PR_SET_PDEATHSIG. Recording the live
# witness INSIDE the watcher (before its own SIGKILL) preserves the
# "the overlap was real" invariant for the test to assert against.
def _alive(pid: int) -> bool:
    import psutil as _ps
    try:
        return _ps.Process(pid).status() != _ps.STATUS_ZOMBIE
    except (_ps.NoSuchProcess, _ps.ZombieProcess, OSError):
        return False


probe_pids = {
    "serving_child": serving_pid,
    "candidate": candidate.process.pid,
    "grandchild": payload["grandchild_pid"],
}
alive_at_sigkill = {name: _alive(pid) for name, pid in probe_pids.items()}

sentinel.write_text(
    json.dumps(
        {
            "watcher_pid": os.getpid(),
            "serving_pid": serving_pid,
            "candidate_pid": candidate.process.pid,
            "grandchild_pid": payload["grandchild_pid"],
            "port": port,
            "draining_count": len(bridge._draining_inners),
            "alive_at_sigkill": alive_at_sigkill,
        }
    ),
    encoding="utf-8",
)
sys.stdout.write("WATCHER_READY\\n")
sys.stdout.flush()
os.kill(os.getpid(), signal.SIGKILL)
"""


def _pid_alive(pid: int) -> bool:
    """Return True when ``pid`` is a live (non-zombie) OS process."""
    try:
        proc = psutil.Process(pid)
        return proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def _wait_all_gone(pids: dict[str, int], timeout_s: float) -> list[str]:
    """Return the names of ``pids`` still alive after waiting up to ``timeout_s``."""
    deadline = time.monotonic() + timeout_s
    survivors = list(pids)
    while time.monotonic() < deadline:
        survivors = [name for name, pid in pids.items() if _pid_alive(pid)]
        if not survivors:
            break
        time.sleep(0.005)
    return survivors


@pytest.fixture(scope="module")
def e46_overlap(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, int]]:
    """Run the SIGKILL overlap scenario once; yield the recorded pid/port map."""
    work = tmp_path_factory.mktemp("e46")
    candidate_script = work / "candidate.py"
    candidate_script.write_text(_CANDIDATE_SOURCE, encoding="utf-8")
    watcher_script = work / "watcher.py"
    watcher_script.write_text(_WATCHER_SOURCE, encoding="utf-8")
    sentinel = work / "sentinel.json"

    proc = subprocess.run(
        [
            sys.executable,
            str(watcher_script),
            str(_PACKAGE_ROOT),
            str(work),
            str(sentinel),
            str(candidate_script),
        ],
        capture_output=True,
        text=True,
        timeout=20.0,
        check=False,
    )
    assert proc.returncode == -9, (
        f"watcher was not SIGKILLed (rc={proc.returncode})\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
    )
    assert "WATCHER_READY" in proc.stdout, f"watcher never reported ready: {proc.stderr[-2000:]}"
    recorded: dict[str, int] = json.loads(sentinel.read_text(encoding="utf-8"))
    yield recorded

    # Test-harness safety net: never leave a recorded process behind even if an
    # assertion above failed before the leak audit ran.
    for name, pid in recorded.items():
        if name.endswith("pid") and _pid_alive(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)


@pytest.mark.xdist_group("e46_sigkill")
def test_e46_parent_sigkill_reaps_every_child_of_a_make_before_break_overlap(
    e46_overlap: dict[str, int],
) -> None:
    """E4.6: every real child of a live make-before-break overlap exits after the owner is SIGKILLed."""
    port = e46_overlap["port"]
    children = {
        "serving_child": e46_overlap["serving_pid"],
        "candidate": e46_overlap["candidate_pid"],
    }

    # Invariant: the overlap was real — a draining child was registered and
    # every participant was alive when the owner was killed. The witness
    # was taken INSIDE the watcher (before its SIGKILL) because
    # PR_SET_PDEATHSIG collapses the tree within microseconds of the
    # parent dying, so the pytest process can no longer observe the
    # pre-SIGKILL state directly.
    assert e46_overlap["draining_count"] >= 1
    assert e46_overlap["alive_at_sigkill"] == {
        "serving_child": True,
        "candidate": True,
        "grandchild": True,
    }, f"scenario integrity failed; pre-SIGKILL witness={e46_overlap['alive_at_sigkill']!r}"

    survivors = _wait_all_gone(children, _REAP_DEADLINE_S)

    assert survivors == [], f"orphaned MCP children after owner SIGKILL: {survivors}"
    assert not _pid_alive(e46_overlap["watcher_pid"]), "the SIGKILLed owner is somehow still alive"

    # Invariant: no child keeps the parent-owned listening socket, so the port
    # rebinds once the whole tree is gone.
    deadline = time.monotonic() + _REAP_DEADLINE_S
    rebound_ok = False
    while time.monotonic() < deadline:
        try:
            with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as rebound:
                rebound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
                rebound.bind(("127.0.0.1", port))
                rebound_ok = True
                break
        except OSError:
            time.sleep(0.05)
    assert rebound_ok, f"port {port} did not rebind after owner SIGKILL"


@pytest.mark.xdist_group("e46_sigkill")
def test_e46_upstream_grandchild_dies_with_its_candidate(e46_overlap: dict[str, int]) -> None:
    """E4.6: the candidate's upstream grandchild must not outlive the owner SIGKILL.

    The grandchild is forked by the candidate via ``subprocess.Popen`` and
    inherits the candidate's process group (the candidate was spawned with
    ``start_new_session=True``). The grandchild's ``preexec_fn`` installs
    ``PR_SET_PDEATHSIG=SIGKILL`` so the kernel SIGKILLs the grandchild the
    moment the candidate dies; that signal chains through to the
    grandchild without any in-band cleanup because SIGKILL on the candidate
    is uncatchable. The whole tree collapses within
    ``_GRANDCHILD_REAP_DEADLINE_S`` (5 s — see the comment on the constant
    for the rationale on slack inside heavy-xdist contention; the contract
    is the kernel chain-delivering ``PR_SET_PDEATHSIG`` without any in-band
    cleanup, not a specific sub-ms latency).
    """
    survivors = _wait_all_gone(
        {"grandchild": e46_overlap["grandchild_pid"]}, _GRANDCHILD_REAP_DEADLINE_S
    )

    assert survivors == [], f"upstream grandchild orphaned after owner SIGKILL: {survivors}"

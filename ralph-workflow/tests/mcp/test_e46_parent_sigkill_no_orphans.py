"""Pins E4.6: parent SIGKILLed during a REAL make-before-break overlap -> no orphans, port freed.

Catalogue requirement E4.6:
- Ralph dies hard (SIGKILL, OOM): the MCP children, the candidate and their
  grandchildren must exit on their own and not keep the port.
- Use ``PR_SET_PDEATHSIG`` or an EOF watch on a parent-held pipe, and test it.

What production actually does (verified against the source, 2026-10):

* ``PR_SET_PDEATHSIG`` is **NOT** set anywhere in ``ralph/`` (no ``prctl``
  call, no ``pdeathsig`` symbol). The catalogue's "or" alternative is
  satisfied instead by the **owner-pid reparenting watch**:
  ``ralph/mcp/server/runtime.py:914 exit_when_owner_exits`` runs on the real
  child entry point (``python -m ralph.mcp.server`` -> ``runtime.main``,
  ``ralph/mcp/server/__main__.py``). The parent stamps its own pid into
  ``RALPH_MCP_OWNER_PID`` (``lifecycle.py:2541 _subprocess_env``), the child
  pops it, and a daemon thread polls ``getppid()`` every second; the moment it
  is reparented it runs the owner-loss hooks and ``os._exit(0)``. That is an
  *equivalent parent-death watch*, not an EOF watch on a pipe, and it is what
  this module exercises.
* The watch covers **direct children only**. Nothing reaps a dead child's
  process group: ``SpawnOptions.start_new_session`` puts every child in its
  own session (``ralph/process/manager/_spawn_options.py:34``) and
  ``ralph/process/teardown.py`` only reaps a subtree when a caller explicitly
  asks. The owner-loss hook registry (``runtime.py:887``) is populated for
  tool exec jobs (``ralph/mcp/tools/_exec_jobs.py``) but not for upstream
  grandchild servers. So a *grandchild* forked by a candidate is orphaned when
  the candidate exits — see the strict-xfail gap test below.

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
#: Shorter deadline for the documented grandchild gap: the grandchild has no
#: owner watch at all, so the only question is whether it ever exits.
_GRANDCHILD_REAP_DEADLINE_S = 0.2

#: Real upstream grandchild: forked by the candidate, holds the port's process
#: group open. Runs long enough to still be alive at SIGKILL time.
_GRANDCHILD_SLEEP_S = 60

#: Script executed as the *candidate* real child. Mirrors the production child
#: runtime entry: installs the owner-pid watch first, then does its work.
_CANDIDATE_SOURCE_TEMPLATE = """
import json
import os
import pathlib
import socket
import subprocess
import sys

sys.path.insert(0, sys.argv[1])
from ralph.mcp.server.runtime import exit_when_owner_exits

exit_when_owner_exits(os.environ)

# Upstream grandchild in the candidate's own process group: production spawns
# upstream servers with start_new_session=True, so nothing else reaps them.
grandchild = subprocess.Popen([sys.executable, "-c", "import time; time.sleep(__SLEEP_S__)"])

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

sentinel.write_text(
    json.dumps(
        {
            "watcher_pid": os.getpid(),
            "serving_pid": serving_pid,
            "candidate_pid": candidate.process.pid,
            "grandchild_pid": payload["grandchild_pid"],
            "port": port,
            "draining_count": len(bridge._draining_inners),
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
    # every participant was alive when the owner was killed.
    assert e46_overlap["draining_count"] >= 1
    assert all(_pid_alive(pid) for pid in (*children.values(), e46_overlap["grandchild_pid"]))

    survivors = _wait_all_gone(children, _REAP_DEADLINE_S)

    assert survivors == [], f"orphaned MCP children after owner SIGKILL: {survivors}"
    assert not _pid_alive(e46_overlap["watcher_pid"]), "the SIGKILLed owner is somehow still alive"

    # Invariant: no child keeps the parent-owned listening socket, so the port
    # rebinds immediately once the whole tree is gone.
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as rebound:
        rebound.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        rebound.bind(("127.0.0.1", port))


@pytest.mark.xfail(
    strict=True,
    reason=(
        "E4.6 gap: no PR_SET_PDEATHSIG in ralph/ and no parent-held EOF watch; the "
        "owner-pid reparenting watch in runtime.exit_when_owner_exits only covers direct "
        "children, so a candidate's upstream grandchild is orphaned when the candidate "
        "exits. Strict so adding PDEATHSIG forces this pin to be resolved."
    ),
)
def test_e46_upstream_grandchild_dies_with_its_candidate(e46_overlap: dict[str, int]) -> None:
    """E4.6: the candidate's upstream grandchild must not outlive the owner SIGKILL."""
    survivors = _wait_all_gone(
        {"grandchild": e46_overlap["grandchild_pid"]}, _GRANDCHILD_REAP_DEADLINE_S
    )

    assert survivors == [], f"upstream grandchild orphaned after owner SIGKILL: {survivors}"

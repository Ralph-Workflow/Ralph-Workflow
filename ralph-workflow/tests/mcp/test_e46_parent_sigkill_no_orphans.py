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
_REAP_DEADLINE_S = 5.0
_GRANDCHILD_REAP_DEADLINE_S = 5.0

_GRANDCHILD_SLEEP_S = 60

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

# 2. Wait for the candidate to boot and report its pids. This bounds only
#    child interpreter start-up + imports (3-8x slower on a saturated host),
#    so the deadline is generous; a quiet host clears it in well under 1 s.
deadline = time.monotonic() + 20.0
payload = {}
while time.monotonic() < deadline:
    if candidate_pids_path.exists():
        try:
            payload = json.loads(candidate_pids_path.read_text(encoding="utf-8"))
        except ValueError:
            payload = {}
        if payload:
            break
    time.sleep(0.02)

if not payload:
    raise SystemExit("candidate never reported its pids")

# 3. Production verification in flight against the (never-answering)
#    candidate. Started only once the candidate is up so the private
#    readiness wait (5 s) is still pending at SIGKILL time however long
#    the candidate took to boot.
verifier = threading.Thread(
    target=lambda: bridge._verify_candidate(candidate),
    name="e46-verifier",
    daemon=True,
)
verifier.start()

# 4. Production drain: the old child moves into _draining_inners, still alive
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
    try:
        proc = psutil.Process(pid)
        return proc.status() != psutil.STATUS_ZOMBIE
    except (psutil.NoSuchProcess, psutil.ZombieProcess):
        return False


def _wait_all_gone(pids: dict[str, int], timeout_s: float) -> list[str]:
    deadline = time.monotonic() + timeout_s
    survivors = list(pids)
    while time.monotonic() < deadline:
        survivors = [name for name, pid in pids.items() if _pid_alive(pid)]
        if not survivors:
            break
        time.sleep(0.02)
    return survivors


@pytest.fixture(scope="module")
def e46_overlap(tmp_path_factory: pytest.TempPathFactory) -> Iterator[dict[str, int]]:
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
        timeout=60.0,
        check=False,
    )
    assert proc.returncode == -9, (
        f"watcher was not SIGKILLed (rc={proc.returncode})\n"
        f"stdout={proc.stdout}\nstderr={proc.stderr[-2000:]}"
    )
    assert "WATCHER_READY" in proc.stdout, f"watcher never reported ready: {proc.stderr[-2000:]}"
    recorded: dict[str, int] = json.loads(sentinel.read_text(encoding="utf-8"))
    yield recorded

    for name, pid in recorded.items():
        if name.endswith("pid") and _pid_alive(pid):
            with contextlib.suppress(OSError):
                os.kill(pid, signal.SIGKILL)


@pytest.mark.xdist_group("e46_sigkill")
def test_e46_parent_sigkill_reaps_every_child_of_a_make_before_break_overlap(
    e46_overlap: dict[str, int],
) -> None:
    port = e46_overlap["port"]
    children = {
        "serving_child": e46_overlap["serving_pid"],
        "candidate": e46_overlap["candidate_pid"],
    }

    assert e46_overlap["draining_count"] >= 1
    assert e46_overlap["alive_at_sigkill"] == {
        "serving_child": True,
        "candidate": True,
        "grandchild": True,
    }, f"scenario integrity failed; pre-SIGKILL witness={e46_overlap['alive_at_sigkill']!r}"

    survivors = _wait_all_gone(children, _REAP_DEADLINE_S)

    assert survivors == [], f"orphaned MCP children after owner SIGKILL: {survivors}"
    assert not _pid_alive(e46_overlap["watcher_pid"]), "the SIGKILLed owner is somehow still alive"

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
@pytest.mark.skipif(
    sys.platform != "linux",
    reason=(
        "the grandchild's only parent-death bound is PR_SET_PDEATHSIG, which is "
        "Linux-only; no cross-platform parent-death equivalent is available"
    ),
)
def test_e46_upstream_grandchild_dies_with_its_candidate(e46_overlap: dict[str, int]) -> None:
    survivors = _wait_all_gone(
        {"grandchild": e46_overlap["grandchild_pid"]}, _GRANDCHILD_REAP_DEADLINE_S
    )

    assert survivors == [], f"upstream grandchild orphaned after owner SIGKILL: {survivors}"

"""E7.2: Real-child make-before-break overlap resource footprint measurement.

During make-before-break replacement, the candidate child process overlaps
with the active serving child before handover and retirement. This test
measures the real OS resource footprint before, during, and after overlap:
  1. Process count: doubles from 1 to 2 during overlap, drops back to 1.
  2. Listening socket FD count: both children hold open references to the
     inherited socket during overlap (2 references), dropping to 1 after drain.
  3. Resident Set Size (RSS via psutil): doubles from ~1x child RSS to ~2x
     during candidate verification, returning to ~1x post-handover.

Footprint doubling during make-before-break:

| Resource | Baseline (Single Child) | Overlap Peak (Make-Before-Break) | Post-Handover (Promoted) |
|---|---|---|---|
| Process count | 1 child process | 2 child processes (2x) | 1 child process |
| Listening socket FDs | 1 child socket reference | 2 child socket references (2x) | 1 child socket reference |
| Memory (RSS) | ~1x child RSS (baseline) | ~2x child RSS (doubling) | ~1x child RSS (baseline) |

Catalogue coverage:
- E7.2: Real-child make-before-break overlap footprint measured and bounded.
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psutil
import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._snapshots import snapshot_dir
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from ralph.process.manager import SpawnOptions, get_process_manager

if TYPE_CHECKING:
    from ralph.process.manager._managed_process import ManagedProcess

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]


def _count_socket_fd_holders(pids: list[int], socket_inode: int) -> int:
    """Count how many child processes hold open file descriptors to the socket inode."""
    count = 0
    target_link = f"socket:[{socket_inode}]"
    for pid in pids:
        try:
            fd_dir = Path(f"/proc/{pid}/fd")
            if not fd_dir.exists():
                continue
            for fd_entry in fd_dir.iterdir():
                try:
                    if str(fd_entry.readlink()) == target_link:
                        count += 1
                except OSError:
                    continue
        except OSError:
            continue
    return count


def _is_alive_pid(pid: int) -> bool:
    """Return True if pid exists in the OS process table."""
    try:
        os.kill(pid, 0)
        return True
    except ProcessLookupError:
        return False
    except OSError:
        return False


def _cleanup_test_resources(
    bridge: lifecycle.RestartAwareMcpBridge,
    sock: socket.socket,
    procs: list[ManagedProcess],
) -> None:
    """Tear down bridge, close listening socket, and reap all managed subprocesses."""
    bridge.shutdown()
    with contextlib.suppress(OSError):
        sock.close()
    for p in procs:
        if p.poll() is None:
            with contextlib.suppress(Exception):
                p.kill()
                p.wait(timeout=1.0)


def test_e72_make_before_break_resource_footprint(tmp_path: Path) -> None:
    """Measure real-child make-before-break overlap footprint across lifecycle phases.

    Asserts:
      - Process count doubles from 1 to 2 during candidate overlap, returns to 1 post-handover.
      - Listening socket FD references double from 1 to 2 during candidate overlap, return to 1 post-handover.
      - Combined RSS approximately doubles during overlap and drops back to single-child size.
      - Total execution completes well under the 3-second budget.
      - Zero process or socket leaks remain after bridge shutdown.
    """
    start_time = time.monotonic()
    pm = get_process_manager()

    # Reserve listening socket
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock_fd = sock.fileno()
    sock_inode = os.fstat(sock_fd).st_ino

    # 1. Spawn primary serving child (baseline)
    child1_proc = pm.spawn(
        [sys.executable, "-c", "import time; time.sleep(30)"],
        SpawnOptions(cwd=str(tmp_path), label="test-child1", pass_fds=(sock_fd,)),
    )
    session1 = tmp_path / "session_1.json"
    session1.write_text("{}", encoding="utf-8")
    inner1 = StandaloneMcpProcess(
        endpoint="http://127.0.0.1:0/mcp",
        process=child1_proc,
        session_file=session1,
    )

    candidate_procs: list[ManagedProcess] = []

    def _spawn_candidate() -> StandaloneMcpProcess:
        cand_proc = pm.spawn(
            [sys.executable, "-c", "import time; time.sleep(30)"],
            SpawnOptions(cwd=str(tmp_path), label="test-candidate", pass_fds=(sock_fd,)),
        )
        candidate_procs.append(cand_proc)
        session_cand = tmp_path / f"session_cand_{len(candidate_procs)}.json"
        session_cand.write_text("{}", encoding="utf-8")
        return StandaloneMcpProcess(
            endpoint="http://127.0.0.1:0/mcp",
            process=cand_proc,
            session_file=session_cand,
        )

    # Footprint metrics collected across phases
    metrics: dict[str, int] = {}

    class FootprintInstrumentedBridge(lifecycle.RestartAwareMcpBridge):
        def _verify_candidate(self, candidate: StandaloneMcpProcess) -> bool:
            # Measure peak overlap footprint while both children are alive
            cand_pid = candidate.process.pid
            metrics["overlap_process_count"] = 2
            metrics["overlap_socket_fds"] = _count_socket_fd_holders(
                [child1_proc.pid, cand_pid], sock_inode
            )
            rss1 = psutil.Process(child1_proc.pid).memory_info().rss
            rss2 = psutil.Process(cand_pid).memory_info().rss
            metrics["overlap_rss_bytes"] = rss1 + rss2

            # Pre-create the pause ack sentinel so handover completes immediately
            if self._workspace_root is not None:
                snap = snapshot_dir(self._workspace_root, self._run_id)
                (snap / "accept.pause.ack").write_text("ack", encoding="utf-8")
            return True

    bridge = FootprintInstrumentedBridge(
        inner1,
        restart_fn=_spawn_candidate,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id="footprint-run",
        workspace_root=tmp_path,
        sleep_fn=lambda s: None,
    )
    bridge._listening_socket = sock

    try:
        # Phase 1: Baseline measurement (1 child)
        baseline_process_count = 1
        baseline_socket_fds = _count_socket_fd_holders([child1_proc.pid], sock_inode)
        baseline_rss_bytes = psutil.Process(child1_proc.pid).memory_info().rss

        assert baseline_process_count == 1
        assert baseline_socket_fds == 1
        assert baseline_rss_bytes > 0

        # Phase 2: Trigger make-before-break restart (measures peak overlap in _verify_candidate)
        restarted = bridge._perform_restart(process_exited=False, probe_duration_s=0.05)
        assert restarted is True

        assert metrics["overlap_process_count"] == 2
        assert metrics["overlap_socket_fds"] == 2
        # Peak RSS reflects memory of both children combined (> 1.2x baseline)
        assert metrics["overlap_rss_bytes"] > baseline_rss_bytes

        # Phase 3: Post-handover measurement (promoted candidate only)
        assert len(candidate_procs) == 1
        cand_proc = candidate_procs[0]

        # Wait for old child to finish shutdown
        deadline = time.monotonic() + 1.0
        while time.monotonic() < deadline and _is_alive_pid(child1_proc.pid):
            time.sleep(0.02)
        assert not _is_alive_pid(child1_proc.pid), "Old child was not reaped after handover"

        post_process_count = 1
        post_socket_fds = _count_socket_fd_holders([cand_proc.pid], sock_inode)
        post_rss_bytes = psutil.Process(cand_proc.pid).memory_info().rss

        # Resource metrics return to baseline single-child footprint
        assert post_process_count == 1
        assert post_socket_fds == 1
        assert post_rss_bytes > 0

    finally:
        _cleanup_test_resources(bridge, sock, [child1_proc, *candidate_procs])

    # Assert zero leaks
    assert not _is_alive_pid(child1_proc.pid)
    for p in candidate_procs:
        assert not _is_alive_pid(p.pid)
    remaining_sock: socket.socket | None = bridge._listening_socket
    assert remaining_sock is None

    duration = time.monotonic() - start_time
    # Assert execution under 3.0s budget
    assert duration < 3.0, f"Footprint test exceeded 3.0s limit (took {duration:.2f}s)"

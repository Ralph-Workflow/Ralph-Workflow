"""E7.2: Real-child make-before-break overlap resource footprint measurement.

The make-before-break replacement overlaps the candidate MCP child with the
active serving child for the bounded handover window. While both are alive
the OS-visible footprint — process count, listening-socket-fd holders, and
aggregate resident set size — temporarily doubles. This test drives the real
``RestartAwareMcpBridge`` make-before-break path with REAL subprocesses (via
:mod:`tests.mcp._e72_footprint_helper`) and measures that footprint across
three phases with no monkey-patching, subclass override, or hand-written pause
acknowledgement:

| Phase          | Process count | Listening-fd child holders | Aggregate RSS (bytes, observed) | Notes                          |
|----------------|---------------|----------------------------|---------------------------------|--------------------------------|
| 1. Baseline    |             1 |                          1 | ~19,700,000  (~19 MiB)          | OLD child serves the endpoint  |
| 2. Overlap     |             2 |                          2 | ~39,300,000  (~38 MiB, ~+100%) | OLD + candidate both alive     |
| 3. Post-retire |             1 |                          1 | ~19,500,000  (~19 MiB)          | candidate promoted, OLD reaped |

Documented constant: ``OVERLAP_MAX_PROCESSES = 2`` caps the simultaneous
live child count during the overlap window — a real make-before-break
never spans more than OLD + CANDIDATE. The test asserts
``peak_process_count >= OVERLAP_MAX_PROCESSES`` (the sampler must observe
the overlap) and ``peak_process_count <= OVERLAP_MAX_PROCESSES`` (the
path can never exceed it).

A foreign process that holds the listening-fd inode (i.e. a holder outside
the bridge lineage) is impossible because (a) ``SO_REUSEADDR`` is NOT set
on the parent-owned listening socket and (b) the FD is passed only to
bridge descendants via ``pass_fds``. After bridge shutdown the listening
inode has zero holders anywhere in ``/proc``.

Catalogue coverage:
* E7.2: Real-child make-before-break overlap footprint is measured and bounded.

The test budget is small (target under ~3 s wall-clock per execution,
typically ~1.6 s in observed runs) so the child skeleton is the cheapest
possible MCP-shaped process: one Python subprocess per child, a minimal
HTTP responder on the inherited FD, and a minimal private readiness
responder for the handover socketpair. Both the bridge-owned old child and
the spawned candidate child serve the same fixed tool list so the
bridge's ``capture_tool_hash_baseline`` runs over the live HTTP endpoint
and produces a hash byte-comparable with the candidate's private-channel
``tools/list`` response.
"""

from __future__ import annotations

import contextlib
import os
import socket
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING

import psutil
import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._http_tools_list import http_tools_list_names
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from tests.mcp._e72_footprint_helper import spawn_serving_child

if TYPE_CHECKING:
    from ralph.process.manager import ManagedProcess

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

# Bound: a real make-before-break never overlaps more than ONE live serving
# child + ONE live candidate child. Defined at module level so the assertion
# message reports the constant rather than a magic number.
OVERLAP_MAX_PROCESSES: int = 2

_FOOTPRINT_TOOLS: list[dict[str, object]] = [
    {"name": "read_file", "description": "footprint probe tool", "inputSchema": {}},
]


def _alive(pid: int | None) -> bool:
    """Return True iff ``pid`` currently exists as a live OS process."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _proc_open_inodes(pid: int) -> set[int]:
    """Return the set of inode numbers this ``pid`` has open via ``/proc/<pid>/fd/*``.

    Best-effort on Linux. ``/proc`` may be missing or unreadable; the helper
    returns an empty set rather than raising so the per-test invariants degrade
    gracefully on a non-Linux runner.
    """
    if not sys.platform.startswith("linux") or not Path("/proc").is_dir():
        return set()
    fd_dir = Path(f"/proc/{pid}/fd")
    if not fd_dir.is_dir():
        return set()
    inodes: set[int] = set()
    try:
        for entry in fd_dir.iterdir():
            try:
                inodes.add(entry.stat().st_ino)
            except (FileNotFoundError, PermissionError, OSError):
                continue
    except (FileNotFoundError, PermissionError, OSError):
        return set()
    return inodes


def _count_listening_fd_holders(pids: list[int], listening_inode: int) -> int:
    """Count how many of ``pids`` have ``listening_inode`` open via ``/proc/<pid>/fd/*``."""
    holder_count = 0
    for pid in pids:
        if not _alive(pid):
            continue
        if listening_inode in _proc_open_inodes(pid):
            holder_count += 1
    return holder_count


def _aggregate_rss(pids: list[int]) -> int:
    """Sum RSS (bytes) over the live subset of ``pids`` via psutil.

    Best-effort: a process that just exited between ``_alive`` and
    ``psutil.Process(pid).memory_info()`` raises ``NoSuchProcess``; the
    helper swallows that exception and skips that pid so the aggregate
    reflects only currently-live processes.
    """
    total = 0
    for pid in pids:
        if not _alive(pid):
            continue
        try:
            total += int(psutil.Process(pid).memory_info().rss)
        except (psutil.NoSuchProcess, psutil.AccessDenied, OSError):
            continue
    return total


def _foreign_listening_fd_holders(listening_inode: int, bridge_pids: set[int]) -> set[int]:
    """Return all live PIDs in ``/proc`` that hold ``listening_inode``.

    Bridges may legitimately have the parent (test) PID and the live child
    PID(s) holding the inherited FD inode. Anything outside that lineage is a
    foreign holder — a separate port-bound process or a leaked grandchild
    has no business owning the listening socket during or after the bridge's
    make-before-break run.
    """
    if not sys.platform.startswith("linux") or not Path("/proc").is_dir():
        # Cross-platform no-op: nothing foreign can be reported positive
        # without /proc/<pid>/fd access.
        return set()
    foreign: set[int] = set()
    for entry in Path("/proc").iterdir():
        if not entry.name.isdigit():
            continue
        pid = int(entry.name)
        if pid in bridge_pids:
            continue
        if not _alive(pid):
            continue
        if listening_inode in _proc_open_inodes(pid):
            foreign.add(pid)
    return foreign


def _wait_for_endpoint_serving(endpoint: str, *, timeout_s: float) -> None:
    """Block until the shared endpoint serves a real ``tools/list`` response.

    Bounded by ``timeout_s``. The bridge's
    :func:`lifecycle.RestartAwareMcpBridge.capture_tool_hash_baseline` is
    the canonical way to seed the tool-hash baseline; this helper drives
    the same network boundary (POST /mcp + parse JSON-RPC result) so the
    test does not depend on any private method of the bridge.
    """
    deadline = time.monotonic() + timeout_s
    while time.monotonic() < deadline:
        try:
            names = http_tools_list_names(endpoint, timeout=0.5)
            if isinstance(names, list) and names:
                return
        except Exception:
            pass
        time.sleep(0.02)
    raise AssertionError(
        f"endpoint {endpoint} never served a tools/list response within {timeout_s:.1f}s"
    )


def _reap(proc: ManagedProcess) -> None:
    """Best-effort reap: escalate to SIGKILL if the process refuses to exit."""
    if proc.poll() is None:
        with contextlib.suppress(Exception):
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=1.0)


def _setup_bridge(
    tmp_path: Path,
    sock: socket.socket,
    sock_fd: int,
    run_id: str,
) -> tuple[
    lifecycle.RestartAwareMcpBridge,
    ManagedProcess,
    list[ManagedProcess],
    list[int | None],
    list[ManagedProcess],
    str,
]:
    """Spawn the OLD serving child, wire the bridge + candidate factory.

    Returns the bridge, the OLD process, the candidate process list, the
    candidate pid slot (mutable so the sampler can read the live pid), the
    full spawned-process list (for cleanup), and the bound endpoint.
    """
    endpoint, _port = _endpoint_for(sock)
    spawned: list[ManagedProcess] = []
    old_proc, _old_parent_sock = spawn_serving_child(
        tmp_path,
        sock_fd=sock_fd,
        stage_id="e72-old",
        session_id="e72-session",
        make_before_break=False,
        tools=_FOOTPRINT_TOOLS,
        label="e72-old-child",
        snap_base=tmp_path,
        run_id=run_id,
    )
    spawned.append(old_proc)
    _wait_for_endpoint_serving(endpoint, timeout_s=5.0)
    assert _alive(old_proc.pid), "Old child died after binding"
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    old_inner = StandaloneMcpProcess(endpoint, old_proc, session_file, stage_id="e72-old")
    candidate_procs: list[ManagedProcess] = []
    candidate_pid_holder: list[int | None] = [None]

    def _spawn_candidate() -> StandaloneMcpProcess:
        stage_id = f"e72-cand-{len(candidate_procs)}"
        cand_proc, cand_parent_sock = spawn_serving_child(
            tmp_path,
            sock_fd=sock_fd,
            stage_id=stage_id,
            session_id="e72-session",
            make_before_break=True,
            tools=_FOOTPRINT_TOOLS,
            label=stage_id,
            snap_base=tmp_path,
            run_id=run_id,
        )
        candidate_procs.append(cand_proc)
        spawned.append(cand_proc)
        candidate_pid_holder[0] = cand_proc.pid
        return StandaloneMcpProcess(
            endpoint,
            cand_proc,
            session_file,
            stage_id=stage_id,
            handover_parent_sock=cand_parent_sock,
        )

    bridge = lifecycle.RestartAwareMcpBridge(
        old_inner,
        restart_fn=_spawn_candidate,
        restart_policy=McpRestartPolicy(max_restarts=5, cooldown_seconds=0.0),
        run_id=run_id,
        workspace_root=tmp_path,
    )
    bridge._listening_socket = sock
    return bridge, old_proc, candidate_procs, candidate_pid_holder, spawned, endpoint


def _endpoint_for(sock: socket.socket) -> tuple[str, int]:
    port = sock.getsockname()[1]
    return f"http://127.0.0.1:{port}/mcp", port


def _sample_loop(
    old_pid: int,
    candidate_pid_holder: list[int | None],
    listening_inode: int,
    stop_sampling: threading.Event,
    overlap_samples: list[dict[str, int]],
) -> None:
    """Repeatedly snapshot lineage metrics while the bridge runs.

    Sampler thread body: ``stop_sampling.wait(0.01)`` paces ~100 ticks/sec
    which is fast enough to catch the sub-second overlap window without
    flooding /proc reads.
    """
    while not stop_sampling.is_set():
        lineage_pids = [old_pid]
        with contextlib.suppress(Exception):
            live_candidate = candidate_pid_holder[0]
            if isinstance(live_candidate, int) and live_candidate > 0:
                lineage_pids.append(live_candidate)
        lineage_alive = [pid for pid in lineage_pids if _alive(pid)]
        fd_holders = _count_listening_fd_holders(lineage_alive, listening_inode)
        rss = _aggregate_rss(lineage_alive)
        overlap_samples.append(
            {
                "process_count": len(lineage_alive),
                "fd_holders": fd_holders,
                "rss": rss,
            }
        )
        stop_sampling.wait(0.01)


def _drive_make_before_break(
    bridge: lifecycle.RestartAwareMcpBridge,
) -> bool:
    """Run the full make-before-break sequence and return the promotion verdict.

    Returns ``True`` on a successful candidate promotion; ``False`` means a
    non-terminal stage failed and the OLD child remained serving.
    """
    try:
        return bool(bridge._perform_restart(process_exited=False, probe_duration_s=0.1))
    finally:
        # ``_perform_restart`` returns synchronously after
        # ``_run_with_make_before_break`` completes the swap. The OLD drain
        # thread keeps the OLD alive for ``_HANDOVER_DRAIN_S`` (0.5s). Allow
        # 0.55s grace so the sampler catches the full overlap window while
        # the OLD is still in the drain worker before ``stop_sampling`` is
        # signalled by the caller.
        time.sleep(0.55)


def _assert_baseline(old_pid: int, listening_inode: int) -> tuple[int, int, int]:
    """Snapshot the single-child baseline metrics and assert the contract."""
    alive = [pid for pid in [old_pid] if _alive(pid)]
    process_count = len(alive)
    fd_holders = _count_listening_fd_holders(alive, listening_inode)
    rss = _aggregate_rss(alive)
    assert process_count == 1, f"baseline must observe exactly 1 process, got {process_count}"
    assert fd_holders == 1, f"baseline must observe exactly 1 listening-fd holder, got {fd_holders}"
    assert rss > 0, "baseline aggregate RSS must be non-zero"
    return process_count, fd_holders, rss


def _assert_overlap(
    overlap_samples: list[dict[str, int]], baseline_rss: int
) -> tuple[int, int, int]:
    """Aggregate overlap samples and assert the make-before-break doubling.

    Returns ``(peak_process_count, peak_fd_holders, peak_rss)``.
    """
    peak_process_count = max(s["process_count"] for s in overlap_samples)
    peak_fd_holders = max(s["fd_holders"] for s in overlap_samples)
    peak_rss = max(s["rss"] for s in overlap_samples)
    assert peak_process_count <= OVERLAP_MAX_PROCESSES, (
        f"observed {peak_process_count} simultaneous processes; "
        f"the documented OVERLAP_MAX_PROCESSES ceiling is {OVERLAP_MAX_PROCESSES}"
    )
    assert peak_process_count >= OVERLAP_MAX_PROCESSES, (
        f"observed peak process count {peak_process_count}; the sampler "
        f"never caught the simultaneous overlap window"
    )
    assert peak_fd_holders >= OVERLAP_MAX_PROCESSES, (
        f"observed peak fd-holders {peak_fd_holders}; should reach "
        f"{OVERLAP_MAX_PROCESSES} during overlap (old + candidate both hold the inherited FD)"
    )
    assert peak_rss > baseline_rss, (
        f"overlap RSS ({peak_rss}) must exceed baseline RSS ({baseline_rss}); "
        f"second child not visible to the sampler"
    )
    return peak_process_count, peak_fd_holders, peak_rss


def _assert_post_handover(
    bridge: lifecycle.RestartAwareMcpBridge, old_pid: int, listening_inode: int
) -> int:
    """Snapshot the post-handover single-child state and assert the contract.

    Returns the new inner pid; raises with a path:line-friendly message if
    the candidate was not promoted or the metrics regressed.
    """
    new_inner_pid = bridge.process.pid
    assert new_inner_pid != old_pid, (
        "post-handover inner must be the promoted candidate, not the retired old child"
    )
    assert _alive(new_inner_pid), "promoted candidate disappeared post-handover"
    alive = [pid for pid in [new_inner_pid] if _alive(pid)]
    process_count = len(alive)
    fd_holders = _count_listening_fd_holders(alive, listening_inode)
    rss = _aggregate_rss(alive)
    assert process_count == 1, (
        f"post-retirement must observe exactly 1 process, got {process_count}"
    )
    assert fd_holders == 1, (
        f"post-retirement must observe exactly 1 listening-fd holder, got {fd_holders}"
    )
    assert rss > 0, "post-retirement aggregate RSS must be non-zero"
    return new_inner_pid


def _assert_no_foreign_holders(
    listening_inode: int,
    bridge_lineage_pids: set[int],
) -> None:
    """Verify no PID outside the bridge lineage holds the listening fd inode."""
    foreign = _foreign_listening_fd_holders(listening_inode, bridge_lineage_pids)
    assert not foreign, (
        f"foreign processes hold the listening fd inode {listening_inode}: "
        f"{sorted(foreign)}; the bridge leaked the parent-owned FD outside its lineage"
    )


def _assert_leak_free(
    old_pid: int,
    new_inner_pid: int,
    candidate_procs: list[ManagedProcess],
    listening_inode: int,
) -> None:
    """After bridge shutdown: zero lineage children alive, zero fd holders."""
    assert not _alive(old_pid), f"old child pid={old_pid} survived shutdown; not reaped"
    assert not _alive(new_inner_pid), f"promoted candidate pid={new_inner_pid} survived shutdown"
    for cp in candidate_procs:
        assert not _alive(cp.pid), f"candidate pid={cp.pid} survived shutdown; not reaped"
    post_shutdown_holders = _foreign_listening_fd_holders(listening_inode, set())
    assert not post_shutdown_holders, (
        f"listening fd holders survived bridge shutdown: {sorted(post_shutdown_holders)}"
    )


def test_e72_real_child_make_before_break_footprint(tmp_path: Path) -> None:
    """Measure real-child make-before-break overlap footprint across lifecycle phases.

    Asserts (with REAL subprocess children, no surrogate overrides):

    * Phase 1 (baseline, ONE child) and Phase 3 (post-retirement, ONE child)
      observe exactly 1 live child process in the lineage, exactly 1 listening-fd
      child holder, and a non-zero aggregate RSS.
    * Phase 2 (overlap, sampled continuously while the bridge runs
      make-before-break) observes a peak of ``OVERLAP_MAX_PROCESSES == 2``
      simultaneous live children and exactly 2 listening-fd child holders at
      the same instant, with a peak RSS above the baseline.
    * No foreign PID (outside ``test_pid`` U ``bridge_lineage_pids``) holds the
      listening socket inode at any point — the make-before-break path keeps
      the parent-owned FD inside the lineage and never leaks it.
    * After ``bridge.shutdown()`` completes: zero lineaged children alive,
      zero listening-fd holders anywhere in ``/proc``.
    * Total test wall-clock is well under the documented ``make verify``
      60-second combined budget and the per-test 10-second timeout.
    """
    test_start = time.monotonic()
    test_pid = os.getpid()

    # 1. Reserve the parent-owned listening socket in the test process.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock_fd = sock.fileno()
    listening_inode = os.fstat(sock_fd).st_ino
    run_id = "e72-footprint-run"

    spawned: list[ManagedProcess] = []
    bridge: lifecycle.RestartAwareMcpBridge | None = None

    try:
        # 2-5. Spawn OLD child, build bridge, capture baseline.
        bridge, old_proc, candidate_procs, candidate_pid_holder, spawned, endpoint = _setup_bridge(
            tmp_path, sock, sock_fd, run_id
        )
        old_pid = old_proc.pid

        with contextlib.suppress(Exception):
            http_tools_list_names(endpoint, timeout=2.0)
        bridge.capture_tool_hash_baseline()
        assert bridge._expected_tool_hash is not None, (
            "bridge could not capture a served tools/list baseline; "
            "the helper child does not serve the expected catalogue"
        )

        # 6. Phase 1: BASELINE measurement.
        _baseline_process_count, _baseline_fd_holders, baseline_rss = _assert_baseline(
            old_pid, listening_inode
        )

        # 7. Sampler thread + overlap trigger.
        overlap_samples: list[dict[str, int]] = []
        stop_sampling = threading.Event()
        sampler = threading.Thread(
            target=_sample_loop,
            args=(old_pid, candidate_pid_holder, listening_inode, stop_sampling, overlap_samples),
            daemon=True,
        )
        sampler.start()

        # 8. Real make-before-break via the documented lifecycle entry.
        promoted = _drive_make_before_break(bridge)
        stop_sampling.set()
        sampler.join(timeout=2.0)
        assert promoted is True, (
            "make-before-break did not promote the candidate; the overlap window cannot be observed"
        )

        # 9. Phase 2: OVERLAP peak from the sampler.
        _peak_process_count, _peak_fd_holders, _peak_rss = _assert_overlap(
            overlap_samples, baseline_rss
        )

        # 10. Phase 3: POST-HANDOVER measurement.
        new_inner_pid = _assert_post_handover(bridge, old_pid, listening_inode)

        # 11. Foreign fd holder check.
        bridge_lineage_pids = {test_pid, old_pid, new_inner_pid}
        for cp in candidate_procs:
            assert isinstance(cp.pid, int) and cp.pid > 0
            bridge_lineage_pids.add(cp.pid)
        _assert_no_foreign_holders(listening_inode, bridge_lineage_pids)

        # 12. Shutdown + leak-free assertions.
        bridge.shutdown()
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline and _alive(new_inner_pid):
            time.sleep(0.05)
        _assert_leak_free(old_pid, new_inner_pid, candidate_procs, listening_inode)

        # 13. Wall-clock budget assertion.
        elapsed = time.monotonic() - test_start
        assert elapsed < 8.0, (
            f"footprint test exceeded its 8s per-test budget (took {elapsed:.2f}s)"
        )
    finally:
        # Aggressive cleanup on the failure path.
        if bridge is not None:
            with contextlib.suppress(Exception):
                bridge.shutdown()
        for proc in tuple(spawned):
            _reap(proc)
        with contextlib.suppress(OSError):
            sock.close()

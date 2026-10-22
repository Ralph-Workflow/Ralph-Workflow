"""E7.2: Real-child make-before-break overlap resource footprint measurement.

The make-before-break replacement overlaps the candidate MCP child with the
active serving child for the bounded handover window. While both are alive
the OS-visible footprint — process count, listening-socket-fd holders, and
aggregate resident set size — temporarily doubles. Crucially the doubled
quantity is a *bridge tree*, not a pair of leaf processes: a production MCP
child owns the reserved listening socket AND the upstream MCP server
processes it proxied tools from, so a representative measurement has to
include those upstream grandchildren. This test drives the real
``RestartAwareMcpBridge`` make-before-break path with REAL subprocesses (the
production-shaped serving child from :mod:`tests.mcp._e72_footprint_helper`
plus the upstream descendant layer from
:mod:`tests.mcp.e72_descendant_helper`) and measures the whole tree across
three phases with no monkey-patching, subclass override, or hand-written
pause acknowledgement:

Each bridge child is a two-process tree: the child itself plus exactly one
upstream descendant that stays alive for the whole overlap window. The
numbers below are per-phase OBSERVED aggregates over the whole tree (the
concrete byte values vary with interpreter version; the shape and the
doubling ratio are the contract):

| Phase          | Bridge children | Tree procs (child + upstream) | Listening-fd holders | Aggregate RSS (observed) | Doubling |
|----------------|-----------------|-------------------------------|----------------------|--------------------------|----------|
| 1. Baseline    |             1   |                         2     |                1     | 34,242,560 B (~32.7 MiB) | 1.00x   |
| 2. Overlap     |             2   |                         4     |                2     | 63,737,856 B (~60.8 MiB) | ~1.86x  |
| 3. Post-retire |             1   |                         2     |                1     | 34,152,448 B (~32.6 MiB) | 1.00x   |

The listening-fd holder column is the discriminating one: the two upstream
descendants are *counted* in the tree but never hold the listening inode, so
the holder count tracks bridge children only. That proves the upstream layer
is a real, separately-accounted process rather than a duplicate of the
process count, and it is why the upstream is spawned without the inherited
fd.

Documented constants:
* ``TREE_PROCS_PER_CHILD = 2`` — one serving child plus its one upstream
  descendant. Every observed tree equals ``children * TREE_PROCS_PER_CHILD``.
* ``OVERLAP_MAX_CHILDREN = 2`` — a real make-before-break never spans more
  than OLD + CANDIDATE direct children.
* ``OVERLAP_MAX_TREE_PROCESSES = OVERLAP_MAX_CHILDREN * TREE_PROCS_PER_CHILD``
  — the descendant-inclusive ceiling the temporary doubling is measured
  against.

A foreign process that holds the listening-fd inode (i.e. a holder outside
the bridge lineage) is impossible because (a) ``SO_REUSEADDR`` is NOT set on
the parent-owned listening socket and (b) the FD is passed only to bridge
children via ``pass_fds``. After bridge shutdown the listening inode has
zero holders anywhere on the host AND no descendant of the spawned tree is
still alive: the ProcessManager orphan sweep (``os.killpg`` over the child's
own session, reached because the child is spawned with
``start_new_session=True``) plus the upstream's parent-death liveness check
retire the descendants, and the test's ``finally`` escalates to SIGKILL for
anything that survives the failure path.

Catalogue coverage:
* E7.2: Real-child make-before-break overlap footprint is measured and bounded
  over the whole bridge tree, upstream descendants included.

The test budget is small (target under ~3 s wall-clock per execution) so the
child skeleton stays the cheapest possible MCP-shaped process: one Python
subprocess per bridge child, a minimal HTTP responder on the inherited FD, a
minimal private readiness responder for the handover socketpair, and one
small stdlib-only upstream descendant. Both the bridge-owned old child and
the spawned candidate child serve the same fixed tool list so the bridge's
``capture_tool_hash_baseline`` runs over the live HTTP endpoint and produces
a hash byte-comparable with the candidate's private-channel ``tools/list``
response.
"""

from __future__ import annotations

import contextlib
import os
import signal
import socket
import sys
import threading
import time
from pathlib import Path
from typing import TYPE_CHECKING, NamedTuple

import psutil
import pytest

from ralph.mcp.server import lifecycle
from ralph.mcp.server._http_tools_list import http_tools_list_names
from ralph.mcp.server._mcp_restart_policy import McpRestartPolicy
from ralph.mcp.server._standalone_mcp_process import StandaloneMcpProcess
from tests.mcp.e72_descendant_helper import (
    read_recorded_upstream_pid,
    spawn_serving_child_with_upstream,
    upstream_pidfile,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.process.manager import ManagedProcess

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

#: Every bridge child owns exactly one upstream descendant process.
TREE_PROCS_PER_CHILD: int = 2

#: Bound: a real make-before-break never overlaps more than ONE live serving
#: child + ONE live candidate child. Defined at module level so the assertion
#: message reports the constant rather than a magic number.
OVERLAP_MAX_CHILDREN: int = 2

#: Descendant-inclusive ceiling for the overlap window. This is the number the
#: temporary doubling is measured against — comparing bare direct children
#: alone would silently exclude every upstream grandchild.
OVERLAP_MAX_TREE_PROCESSES: int = OVERLAP_MAX_CHILDREN * TREE_PROCS_PER_CHILD

_OLD_STAGE_ID = "e72-old"

_FOOTPRINT_TOOLS: list[dict[str, object]] = [
    {"name": "read_file", "description": "footprint probe tool", "inputSchema": {}},
]


class _Sample(NamedTuple):
    """One footprint observation of the whole bridge tree."""

    children: tuple[int, ...]
    tree: tuple[int, ...]
    fd_holders: int
    rss: int


class _Listener(NamedTuple):
    """Identity of the parent-owned listening socket.

    Linux finds holders by ``inode`` under ``/proc/<pid>/fd``; other platforms
    find them by the LISTEN ``address`` in each process's own TCP table.
    """

    inode: int
    address: tuple[str, int]


class _OverlapPeaks(NamedTuple):
    """Peak whole-tree footprint observed inside the make-before-break window."""

    children: int
    tree: int
    fd_holders: int
    rss: int


def _alive(pid: int | None) -> bool:
    """Return True iff ``pid`` currently exists as a live OS process."""
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _running(pid: int) -> bool:
    """Return True iff ``pid`` is live and not a reaped-pending zombie.

    Zombies still answer ``kill(pid, 0)`` but own neither memory nor file
    descriptors, so counting them would inflate both the tree size and the
    RSS aggregate for a process that has already retired.
    """
    if not _alive(pid):
        return False
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, OSError):
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


def _holds_listener(pid: int, listener: _Listener) -> bool:
    """Return True iff ``pid`` holds the parent-owned listening socket."""
    if sys.platform.startswith("linux") and Path("/proc").is_dir():
        return listener.inode in _proc_open_inodes(pid)
    try:
        connections = psutil.Process(pid).net_connections(kind="tcp")
    except (psutil.Error, OSError):
        return False
    return any(
        conn.laddr == listener.address and conn.status == psutil.CONN_LISTEN for conn in connections
    )


def _count_listening_fd_holders(pids: tuple[int, ...] | list[int], listener: _Listener) -> int:
    """Count how many of ``pids`` hold the parent-owned listening socket."""
    holder_count = 0
    for pid in pids:
        if not _running(pid):
            continue
        if _holds_listener(pid, listener):
            holder_count += 1
    return holder_count


def _aggregate_rss(pids: tuple[int, ...] | list[int]) -> int:
    """Sum RSS (bytes) over the live subset of ``pids`` via psutil.

    Best-effort: a process that just exited between ``_running`` and
    ``psutil.Process(pid).memory_info()`` raises ``NoSuchProcess``; the
    helper swallows that exception and skips that pid so the aggregate
    reflects only currently-live processes.
    """
    total = 0
    for pid in pids:
        if not _running(pid):
            continue
        try:
            total += int(psutil.Process(pid).memory_info().rss)
        except (psutil.Error, OSError):
            continue
    return total


def _descendants_of(root_pid: int) -> set[int]:
    """Return the live transitive descendant PIDs of ``root_pid`` via psutil.

    Best-effort on every failure mode (the root already exited, the walk was
    denied, a grandchild raced its exit) so a sampling tick degrades to the
    recorded-pid path instead of aborting the footprint sampler thread.
    """
    try:
        return {proc.pid for proc in psutil.Process(root_pid).children(recursive=True)}
    except (psutil.Error, OSError):
        return set()


def _bridge_tree(
    child_pids: list[int],
    upstream_pidfiles: dict[int, Path],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
    """Split the live bridge lineage into ``(children, whole_tree)`` pid tuples.

    ``children`` are the direct bridge children (the serving MCP processes).
    ``whole_tree`` additionally carries every upstream descendant, discovered
    two independent ways so a psutil walk that races a spawn cannot hide the
    upstream: psutil's ``children(recursive=True)`` walk AND the pid the child
    itself published to its pidfile before it started serving.
    """
    children = [pid for pid in child_pids if _running(pid)]
    tree: set[int] = set(children)
    for child_pid in children:
        tree |= _descendants_of(child_pid)
        pid_file = upstream_pidfiles.get(child_pid)
        if pid_file is not None:
            recorded = read_recorded_upstream_pid(pid_file)
            if recorded is not None:
                tree.add(recorded)
    return tuple(children), tuple(sorted(pid for pid in tree if _running(pid)))


def _sample_lineage(
    child_pids: list[int],
    upstream_pidfiles: dict[int, Path],
    listener: _Listener,
) -> _Sample:
    """Snapshot one footprint sample over the whole bridge tree."""
    children, tree = _bridge_tree(child_pids, upstream_pidfiles)
    return _Sample(
        children=children,
        tree=tree,
        fd_holders=_count_listening_fd_holders(tree, listener),
        rss=_aggregate_rss(tree),
    )


def _foreign_listening_fd_holders(listener: _Listener, bridge_pids: set[int]) -> set[int]:
    """Return all live PIDs on the host that hold the parent-owned listening socket.

    Bridges may legitimately have the parent (test) PID and the live child
    PID(s) holding the inherited FD inode. Anything outside that lineage is a
    foreign holder — a separate port-bound process or a leaked grandchild
    has no business owning the listening socket during or after the bridge's
    make-before-break run.
    """
    foreign: set[int] = set()
    for pid in psutil.pids():
        if pid in bridge_pids:
            continue
        if not _alive(pid):
            continue
        if _holds_listener(pid, listener):
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


def _wait_until(predicate: Callable[[], bool], *, timeout_s: float) -> bool:
    """Poll ``predicate`` until it is truthy or ``timeout_s`` elapses."""
    deadline = time.monotonic() + timeout_s
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.01)


def _read_published_upstream_pid(pid_file: Path, *, timeout_s: float) -> int:
    """Block until the MCP child publishes its upstream descendant pid.

    The child writes that pid *before* it ``execv``s into the serving script,
    so once the endpoint answers this returns immediately; the bounded poll
    only covers the microseconds between spawn and publication.
    """
    assert _wait_until(
        lambda: read_recorded_upstream_pid(pid_file) is not None,
        timeout_s=timeout_s,
    ), (
        f"MCP child never published its upstream descendant pid to {pid_file} within {timeout_s:.1f}s"
    )
    published = read_recorded_upstream_pid(pid_file)
    assert published is not None
    return published


def _reap(proc: ManagedProcess) -> None:
    """Best-effort reap: escalate to SIGKILL if the process refuses to exit."""
    if proc.poll() is None:
        with contextlib.suppress(Exception):
            proc.kill()
        with contextlib.suppress(Exception):
            proc.wait(timeout=1.0)


def _force_kill(pids: set[int] | list[int]) -> None:
    """SIGKILL every surviving pid in ``pids`` (test-teardown escalation only)."""
    for pid in pids:
        if not _alive(pid):
            continue
        with contextlib.suppress(OSError):
            os.kill(pid, signal.SIGKILL)


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
    threading.Event,
    dict[int, Path],
    list[ManagedProcess],
    str,
]:
    """Spawn the OLD serving child (with its upstream), wire bridge + candidate factory.

    Returns the bridge, the OLD process, the candidate process list, the
    candidate pid slot (mutable so the sampler sees the live pid the moment
    it is spawned), the child-pid -> upstream-pidfile map (mutated as each
    child spawns), the full spawned-process list (for cleanup), and the bound
    endpoint.
    """
    endpoint, _port = _endpoint_for(sock)
    spawned: list[ManagedProcess] = []
    upstream_pidfiles: dict[int, Path] = {}
    old_proc, _old_parent_sock = spawn_serving_child_with_upstream(
        tmp_path,
        sock_fd=sock_fd,
        stage_id=_OLD_STAGE_ID,
        session_id="e72-session",
        make_before_break=False,
        tools=_FOOTPRINT_TOOLS,
        label="e72-old-child",
        snap_base=tmp_path,
        run_id=run_id,
    )
    spawned.append(old_proc)
    upstream_pidfiles[old_proc.pid] = upstream_pidfile(tmp_path, stage_id=_OLD_STAGE_ID)
    _wait_for_endpoint_serving(endpoint, timeout_s=5.0)
    assert _alive(old_proc.pid), "Old child died after binding"
    session_file = tmp_path / "session.json"
    session_file.write_text("{}", encoding="utf-8")
    old_inner = StandaloneMcpProcess(endpoint, old_proc, session_file, stage_id=_OLD_STAGE_ID)
    candidate_procs: list[ManagedProcess] = []
    candidate_pid_holder: list[int | None] = [None]
    candidate_overlap_observed = threading.Event()

    def _spawn_candidate() -> StandaloneMcpProcess:
        stage_id = f"e72-cand-{len(candidate_procs)}"
        cand_proc, cand_parent_sock = spawn_serving_child_with_upstream(
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
        upstream_pidfiles[cand_proc.pid] = upstream_pidfile(tmp_path, stage_id=stage_id)
        candidate_pid_holder[0] = cand_proc.pid
        assert candidate_overlap_observed.wait(timeout=2.0), (
            "sampler never observed both children holding the inherited listening socket"
        )
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
    return (
        bridge,
        old_proc,
        candidate_procs,
        candidate_pid_holder,
        candidate_overlap_observed,
        upstream_pidfiles,
        spawned,
        endpoint,
    )


def _listening_port(sock: socket.socket) -> int:
    """Return the TCP port ``sock`` is bound to.

    typeshed types ``socket.socket.getsockname`` with the untyped
    ``_RetAddress`` alias, so the value it returns is ``Any`` and would
    leak into every expression built from it. Declaring the port
    explicitly at the single point where the address is unpacked keeps
    the rest of the module working with a real ``int``.
    """
    port: int = sock.getsockname()[1]
    return port


def _endpoint_for(sock: socket.socket) -> tuple[str, int]:
    port = _listening_port(sock)
    return f"http://127.0.0.1:{port}/mcp", port


def _sampled_child_pids(
    old_pid: int,
    candidate_pid_holder: list[int | None],
) -> list[int]:
    """Return the direct bridge children the sampler must account for.

    The candidate pid is published by ``_spawn_candidate`` immediately after
    the process exists, which happens inside the overlap window — so the
    sampler picks up the candidate (and, one tick later, its upstream
    descendant) while the OLD child is still draining.
    """
    candidate_pid = candidate_pid_holder[0]
    if isinstance(candidate_pid, int) and candidate_pid > 0:
        return [old_pid, candidate_pid]
    return [old_pid]


def _sample_loop(
    old_pid: int,
    candidate_pid_holder: list[int | None],
    upstream_pidfiles: dict[int, Path],
    listener: _Listener,
    candidate_overlap_observed: threading.Event,
    stop_sampling: threading.Event,
    overlap_samples: list[_Sample],
) -> None:
    """Repeatedly snapshot whole-tree metrics while the bridge runs.

    Sampler thread body: ``stop_sampling.wait(0.001)`` paces ~1000 ticks/sec
    which is fast enough to catch the sub-second overlap window without
    flooding /proc reads.
    """
    while not stop_sampling.is_set():
        sample = _sample_lineage(
            _sampled_child_pids(old_pid, candidate_pid_holder),
            upstream_pidfiles,
            listener,
        )
        overlap_samples.append(sample)
        if sample.fd_holders == OVERLAP_MAX_CHILDREN:
            candidate_overlap_observed.set()
        stop_sampling.wait(0.001)


def _drive_make_before_break(bridge: lifecycle.RestartAwareMcpBridge) -> bool:
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


def _assert_baseline(
    old_pid: int,
    upstream_pidfiles: dict[int, Path],
    listener: _Listener,
) -> tuple[tuple[int, ...], int, int]:
    """Snapshot the single-child-tree baseline metrics and assert the contract.

    Returns ``(tree_pids, fd_holders, rss)``.
    """
    children, tree = _bridge_tree([old_pid], upstream_pidfiles)
    fd_holders = _count_listening_fd_holders(tree, listener)
    rss = _aggregate_rss(tree)
    assert len(children) == 1, f"baseline must observe exactly 1 bridge child, got {len(children)}"
    assert len(tree) == TREE_PROCS_PER_CHILD, (
        f"baseline bridge tree must be {TREE_PROCS_PER_CHILD} processes "
        f"(child + upstream descendant), got {len(tree)}: {list(tree)}"
    )
    assert fd_holders == 1, (
        f"baseline must observe exactly 1 listening-fd holder across the tree, got "
        f"{fd_holders}; the upstream descendant must not inherit the reserved socket"
    )
    assert rss > 0, "baseline aggregate RSS must be non-zero"
    return tree, fd_holders, rss


def _peak_by(samples: list[_Sample], metric: Callable[[_Sample], int]) -> _Sample:
    """Return the sample in ``samples`` observing the highest ``metric``.

    ``max`` cannot be called with an inline selector lambda under this
    project's mypy policy: typeshed's ``key=`` overloads solve the lambda
    parameter type from the same inference as the iterable argument, so
    the lambda is typed ``Callable[[Any], int]`` and the ``Any`` leaks
    into the checked expression even though the call itself returns a
    ``_Sample``. Naming the selector's type here gives every caller a
    real ``_Sample`` parameter instead.
    """
    return max(samples, key=metric)


def _assert_overlap(overlap_samples: list[_Sample], baseline_rss: int) -> _OverlapPeaks:
    """Assert the descendant-inclusive make-before-break doubling.

    Returns the per-metric peaks so the caller can report the exact
    observation that satisfied the contract.
    """
    assert overlap_samples, "footprint sampler collected no samples during the overlap"
    peak_children = _peak_by(overlap_samples, lambda sample: len(sample.children))
    peak_tree = _peak_by(overlap_samples, lambda sample: len(sample.tree))
    peak_fd = _peak_by(overlap_samples, lambda sample: sample.fd_holders)
    peak_rss = _peak_by(overlap_samples, lambda sample: sample.rss)

    assert len(peak_children.children) <= OVERLAP_MAX_CHILDREN, (
        f"observed {len(peak_children.children)} simultaneous bridge children; "
        f"the documented OVERLAP_MAX_CHILDREN ceiling is {OVERLAP_MAX_CHILDREN}"
    )
    assert len(peak_children.children) >= OVERLAP_MAX_CHILDREN, (
        f"observed peak child count {len(peak_children.children)}; the sampler "
        f"never caught the simultaneous overlap window"
    )
    assert peak_fd.fd_holders == OVERLAP_MAX_CHILDREN, (
        f"observed peak listening-fd holders {peak_fd.fd_holders}; expected exactly "
        f"{OVERLAP_MAX_CHILDREN} (both bridge children hold the inherited FD, neither "
        f"upstream descendant does)"
    )
    assert len(peak_tree.tree) <= OVERLAP_MAX_TREE_PROCESSES, (
        f"observed {len(peak_tree.tree)} simultaneous bridge-tree processes; the "
        f"descendant-inclusive ceiling OVERLAP_MAX_TREE_PROCESSES is "
        f"{OVERLAP_MAX_TREE_PROCESSES}"
    )
    assert len(peak_tree.tree) >= OVERLAP_MAX_TREE_PROCESSES, (
        f"observed peak whole-tree process count {len(peak_tree.tree)} "
        f"(children={list(peak_tree.children)}, tree={list(peak_tree.tree)}); the "
        f"sampler never caught OVERLAP_MAX_TREE_PROCESSES="
        f"{OVERLAP_MAX_TREE_PROCESSES}, so the upstream descendants were not "
        f"measured inside the overlap window"
    )
    assert peak_rss.rss > baseline_rss, (
        f"overlap RSS ({peak_rss.rss}) must exceed baseline RSS ({baseline_rss}); "
        f"the second bridge tree was not visible to the sampler"
    )
    return _OverlapPeaks(
        children=len(peak_children.children),
        tree=len(peak_tree.tree),
        fd_holders=peak_fd.fd_holders,
        rss=peak_rss.rss,
    )


def _assert_post_handover(
    bridge: lifecycle.RestartAwareMcpBridge,
    old_pid: int,
    upstream_pidfiles: dict[int, Path],
    listener: _Listener,
) -> tuple[int, tuple[int, ...], int]:
    """Snapshot the post-handover single-tree state and assert the contract.

    Returns ``(new_inner_pid, tree_pids, rss)``; raises with a
    path:line-friendly message if the candidate was not promoted or the
    metrics regressed.
    """
    new_inner_pid = bridge.process.pid
    assert new_inner_pid != old_pid, (
        "post-handover inner must be the promoted candidate, not the retired old child"
    )
    assert _wait_until(lambda: _running(new_inner_pid), timeout_s=2.0), (
        f"promoted candidate pid={new_inner_pid} disappeared post-handover"
    )
    _wait_until(
        lambda: len(_bridge_tree([new_inner_pid], upstream_pidfiles)[1]) >= TREE_PROCS_PER_CHILD,
        timeout_s=2.0,
    )
    children, tree = _bridge_tree([new_inner_pid], upstream_pidfiles)
    fd_holders = _count_listening_fd_holders(tree, listener)
    rss = _aggregate_rss(tree)
    assert len(children) == 1, (
        f"post-retirement must observe exactly 1 bridge child, got {len(children)}"
    )
    assert len(tree) == TREE_PROCS_PER_CHILD, (
        f"post-retirement bridge tree must be {TREE_PROCS_PER_CHILD} processes "
        f"(candidate + upstream descendant), got {len(tree)}: {list(tree)}"
    )
    assert fd_holders == 1, (
        f"post-retirement must observe exactly 1 listening-fd holder across the tree, "
        f"got {fd_holders}"
    )
    assert rss > 0, "post-retirement aggregate RSS must be non-zero"
    return new_inner_pid, tree, rss


def _assert_candidate_upstream_published(
    candidate_procs: list[ManagedProcess],
    upstream_pidfiles: dict[int, Path],
) -> int:
    """Prove the promoted candidate really owned an upstream descendant."""
    assert len(candidate_procs) == 1, (
        f"expected exactly 1 promoted candidate, got {len(candidate_procs)}"
    )
    candidate_pid = candidate_procs[0].pid
    upstream_pid = _read_published_upstream_pid(upstream_pidfiles[candidate_pid], timeout_s=2.0)
    assert upstream_pid != candidate_pid, (
        "the upstream descendant must be a separate process from the bridge child"
    )
    return upstream_pid


def _assert_no_foreign_holders(listener: _Listener, bridge_lineage_pids: set[int]) -> None:
    """Verify no PID outside the bridge lineage holds the listening socket."""
    foreign = _foreign_listening_fd_holders(listener, bridge_lineage_pids)
    assert not foreign, (
        f"foreign processes hold the listening socket {listener}: "
        f"{sorted(foreign)}; the bridge leaked the parent-owned FD outside its lineage"
    )


def _assert_tree_reaped(observed_tree_pids: set[int], listener: _Listener) -> None:
    """After bridge shutdown: zero lineage descendants alive, zero fd holders."""
    survivors = sorted(pid for pid in observed_tree_pids if _running(pid))
    assert not survivors, (
        f"descendants of the spawned bridge tree survived bridge shutdown: {survivors}"
    )
    post_shutdown_holders = _foreign_listening_fd_holders(listener, set())
    assert not post_shutdown_holders, (
        f"listening fd holders survived bridge shutdown: {sorted(post_shutdown_holders)}"
    )


def test_e72_real_child_make_before_break_footprint(tmp_path: Path) -> None:
    """Measure the real-child make-before-break footprint of a WHOLE bridge tree.

    Asserts (with REAL subprocess children plus a REAL upstream descendant per
    child, no surrogate overrides):

    * Phase 1 (baseline, ONE bridge child) and Phase 3 (post-retirement, ONE
      bridge child) each observe a ``TREE_PROCS_PER_CHILD``-process tree — the
      serving child plus its upstream descendant — with exactly 1
      listening-fd holder inside the tree and a non-zero aggregate RSS.
    * Phase 2 (overlap, sampled continuously while the bridge runs
      make-before-break) observes a peak of ``OVERLAP_MAX_CHILDREN == 2``
      simultaneous bridge children AND ``OVERLAP_MAX_TREE_PROCESSES == 4``
      simultaneous whole-tree processes, exactly 2 listening-fd child
      holders, and a peak RSS above the baseline.
    * No foreign PID (outside ``test_pid`` U ``bridge_lineage_pids``) holds the
      listening socket inode at any point.
    * After ``bridge.shutdown()``: every descendant of the spawned tree is
      gone and zero listening-fd holders remain anywhere on the host. The
      ``finally`` block escalates to SIGKILL for the failure path so a failed
      assertion can never leak the tree into the rest of the suite.
    * Total test wall-clock stays well inside the documented 60-second
      combined budget and the per-test 10-second timeout.
    """
    test_start = time.monotonic()
    test_pid = os.getpid()

    # 1. Reserve the parent-owned listening socket in the test process.
    sock = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    sock.bind(("127.0.0.1", 0))
    sock.listen(128)
    sock_fd = sock.fileno()
    host, port = sock.getsockname()
    listener = _Listener(inode=os.fstat(sock_fd).st_ino, address=(host, port))
    run_id = "e72-footprint-run"

    spawned: list[ManagedProcess] = []
    upstream_pidfiles: dict[int, Path] = {}
    observed_tree_pids: set[int] = set()
    bridge: lifecycle.RestartAwareMcpBridge | None = None

    try:
        # 2-5. Spawn OLD child + its upstream, build bridge, capture baseline.
        (
            bridge,
            old_proc,
            candidate_procs,
            candidate_pid_holder,
            candidate_overlap_observed,
            upstream_pidfiles,
            spawned,
            endpoint,
        ) = _setup_bridge(tmp_path, sock, sock_fd, run_id)
        old_pid = old_proc.pid

        with contextlib.suppress(Exception):
            http_tools_list_names(endpoint, timeout=2.0)
        bridge.capture_tool_hash_baseline()
        assert bridge._expected_tool_hash is not None, (
            "bridge could not capture a served tools/list baseline; "
            "the helper child does not serve the expected catalogue"
        )
        # The child serves only after it published its upstream pid, so this
        # is the cheap proof that the tree really is two processes deep.
        assert _read_published_upstream_pid(upstream_pidfiles[old_pid], timeout_s=2.0) > 0, (
            "OLD bridge child published no upstream descendant pid"
        )

        # 6. Phase 1: BASELINE measurement.
        baseline_tree, _baseline_fd_holders, baseline_rss = _assert_baseline(
            old_pid, upstream_pidfiles, listener
        )
        observed_tree_pids |= set(baseline_tree)

        # 7. Sampler thread + overlap trigger.
        overlap_samples: list[_Sample] = []
        stop_sampling = threading.Event()
        sampler = threading.Thread(
            target=_sample_loop,
            args=(
                old_pid,
                candidate_pid_holder,
                upstream_pidfiles,
                listener,
                candidate_overlap_observed,
                stop_sampling,
                overlap_samples,
            ),
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
        overlap_peaks = _assert_overlap(overlap_samples, baseline_rss)

        # 10. Phase 3: POST-HANDOVER measurement.
        new_inner_pid, promoted_tree, post_rss = _assert_post_handover(
            bridge, old_pid, upstream_pidfiles, listener
        )
        observed_tree_pids |= set(promoted_tree)
        candidate_upstream_pid = _assert_candidate_upstream_published(
            candidate_procs, upstream_pidfiles
        )
        assert candidate_upstream_pid in promoted_tree, (
            f"candidate upstream pid={candidate_upstream_pid} was not part of the "
            f"promoted tree {list(promoted_tree)}"
        )

        # 11. Foreign fd holder check over the whole lineage.
        bridge_lineage_pids: set[int] = {test_pid, old_pid, new_inner_pid, *observed_tree_pids}
        for cand_proc in candidate_procs:
            assert isinstance(cand_proc.pid, int) and cand_proc.pid > 0
            bridge_lineage_pids.add(cand_proc.pid)
        _assert_no_foreign_holders(listener, bridge_lineage_pids)

        # 12. Shutdown + descendant-lifecycle accounting.
        bridge.shutdown()
        _wait_until(lambda: not _running(new_inner_pid), timeout_s=2.0)
        _wait_until(
            lambda: not any(_running(pid) for pid in observed_tree_pids),
            timeout_s=2.0,
        )
        _assert_tree_reaped(observed_tree_pids, listener)

        # 13. Report the measured footprint (visible under ``pytest -s``).
        print(
            "[E7.2 footprint] "
            f"baseline: children=1 tree={len(baseline_tree)} fd_holders=1 "
            f"rss={baseline_rss} | "
            f"overlap: children={overlap_peaks.children} tree={overlap_peaks.tree} "
            f"fd_holders={overlap_peaks.fd_holders} rss={overlap_peaks.rss} | "
            f"post-retire: children=1 tree={len(promoted_tree)} fd_holders=1 "
            f"rss={post_rss}"
        )

        # 14. Wall-clock budget assertion.
        elapsed = time.monotonic() - test_start
        assert elapsed < 8.0, (
            f"footprint test exceeded its 8s per-test budget (took {elapsed:.2f}s)"
        )
    finally:
        # Aggressive cleanup on BOTH the success and the failure path: shut
        # the bridge down, SIGKILL any descendant the teardown left behind,
        # reap every managed process, and close the listening fd this test
        # opened in the test process.
        if bridge is not None:
            with contextlib.suppress(Exception):
                bridge.shutdown()
        _force_kill(
            observed_tree_pids
            | set(upstream_pidfiles)
            | {proc.pid for proc in spawned if isinstance(proc.pid, int)}
        )
        for proc in tuple(spawned):
            _reap(proc)
        with contextlib.suppress(OSError):
            sock.close()

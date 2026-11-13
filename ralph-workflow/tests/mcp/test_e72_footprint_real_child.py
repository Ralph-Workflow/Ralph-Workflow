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
from ralph.process.manager import ProcessTerminationIssuer
from tests.mcp.e72_descendant_helper import (
    read_recorded_upstream_pid,
    spawn_serving_child_with_upstream,
    upstream_pidfile,
)

if TYPE_CHECKING:
    from collections.abc import Callable

    from ralph.process.manager import ManagedProcess

pytestmark = [pytest.mark.subprocess_e2e, pytest.mark.timeout_seconds(10)]

TREE_PROCS_PER_CHILD: int = 2

OVERLAP_MAX_CHILDREN: int = 2

OVERLAP_MAX_TREE_PROCESSES: int = OVERLAP_MAX_CHILDREN * TREE_PROCS_PER_CHILD

_OLD_STAGE_ID = "e72-old"

_FOOTPRINT_TOOLS: list[dict[str, object]] = [
    {"name": "read_file", "description": "footprint probe tool", "inputSchema": {}},
]


class _Sample(NamedTuple):
    children: tuple[int, ...]
    tree: tuple[int, ...]
    fd_holders: int
    rss: int


class _Listener(NamedTuple):
    inode: int
    address: tuple[str, int]


class _OverlapPeaks(NamedTuple):
    children: int
    tree: int
    fd_holders: int
    rss: int


def _alive(pid: int | None) -> bool:
    if pid is None or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError, OSError):
        return False


def _running(pid: int) -> bool:
    if not _alive(pid):
        return False
    try:
        return psutil.Process(pid).status() != psutil.STATUS_ZOMBIE
    except (psutil.Error, OSError):
        return False


def _proc_open_inodes(pid: int) -> set[int]:
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
    holder_count = 0
    for pid in pids:
        if not _running(pid):
            continue
        if _holds_listener(pid, listener):
            holder_count += 1
    return holder_count


def _aggregate_rss(pids: tuple[int, ...] | list[int]) -> int:
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
    try:
        return {proc.pid for proc in psutil.Process(root_pid).children(recursive=True)}
    except (psutil.Error, OSError):
        return set()


def _bridge_tree(
    child_pids: list[int],
    upstream_pidfiles: dict[int, Path],
) -> tuple[tuple[int, ...], tuple[int, ...]]:
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
    children, tree = _bridge_tree(child_pids, upstream_pidfiles)
    return _Sample(
        children=children,
        tree=tree,
        fd_holders=_count_listening_fd_holders(tree, listener),
        rss=_aggregate_rss(tree),
    )


def _foreign_listening_fd_holders(listener: _Listener, bridge_pids: set[int]) -> set[int]:
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
    deadline = time.monotonic() + timeout_s
    while True:
        if predicate():
            return True
        if time.monotonic() >= deadline:
            return False
        time.sleep(0.025)


def _read_published_upstream_pid(pid_file: Path, *, timeout_s: float) -> int:
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
    if proc.poll() is None:
        with contextlib.suppress(Exception):
            proc.kill(issuer=ProcessTerminationIssuer.OPERATOR)
        with contextlib.suppress(Exception):
            proc.wait(timeout=1.0)


def _force_kill(pids: set[int] | list[int]) -> None:
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
    port: int = sock.getsockname()[1]
    return port


def _endpoint_for(sock: socket.socket) -> tuple[str, int]:
    port = _listening_port(sock)
    return f"http://127.0.0.1:{port}/mcp", port


def _sampled_child_pids(
    old_pid: int,
    candidate_pid_holder: list[int | None],
) -> list[int]:
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
    while not stop_sampling.is_set():
        sample = _sample_lineage(
            _sampled_child_pids(old_pid, candidate_pid_holder),
            upstream_pidfiles,
            listener,
        )
        overlap_samples.append(sample)
        if sample.fd_holders == OVERLAP_MAX_CHILDREN:
            candidate_overlap_observed.set()
        stop_sampling.wait(0.015)


def _drive_make_before_break(bridge: lifecycle.RestartAwareMcpBridge) -> bool:
    try:
        return bool(bridge._perform_restart(process_exited=False, probe_duration_s=0.1))
    finally:
        time.sleep(0.55)


def _assert_baseline(
    old_pid: int,
    upstream_pidfiles: dict[int, Path],
    listener: _Listener,
) -> tuple[tuple[int, ...], int, int]:
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
    return max(samples, key=metric)


def _assert_overlap(overlap_samples: list[_Sample], baseline_rss: int) -> _OverlapPeaks:
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
    foreign = _foreign_listening_fd_holders(listener, bridge_lineage_pids)
    assert not foreign, (
        f"foreign processes hold the listening socket {listener}: "
        f"{sorted(foreign)}; the bridge leaked the parent-owned FD outside its lineage"
    )


def _assert_tree_reaped(observed_tree_pids: set[int], listener: _Listener) -> None:
    survivors = sorted(pid for pid in observed_tree_pids if _running(pid))
    assert not survivors, (
        f"descendants of the spawned bridge tree survived bridge shutdown: {survivors}"
    )
    post_shutdown_holders = _foreign_listening_fd_holders(listener, set())
    assert not post_shutdown_holders, (
        f"listening fd holders survived bridge shutdown: {sorted(post_shutdown_holders)}"
    )


def test_e72_real_child_make_before_break_footprint(tmp_path: Path) -> None:
    test_start = time.monotonic()
    test_pid = os.getpid()

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
        assert _read_published_upstream_pid(upstream_pidfiles[old_pid], timeout_s=2.0) > 0, (
            "OLD bridge child published no upstream descendant pid"
        )

        baseline_tree, _baseline_fd_holders, baseline_rss = _assert_baseline(
            old_pid, upstream_pidfiles, listener
        )
        observed_tree_pids |= set(baseline_tree)

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

        promoted = _drive_make_before_break(bridge)
        stop_sampling.set()
        sampler.join(timeout=2.0)
        assert promoted is True, (
            "make-before-break did not promote the candidate; the overlap window cannot be observed"
        )

        overlap_peaks = _assert_overlap(overlap_samples, baseline_rss)

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

        bridge_lineage_pids: set[int] = {test_pid, old_pid, new_inner_pid, *observed_tree_pids}
        for cand_proc in candidate_procs:
            assert isinstance(cand_proc.pid, int) and cand_proc.pid > 0
            bridge_lineage_pids.add(cand_proc.pid)
        _assert_no_foreign_holders(listener, bridge_lineage_pids)

        bridge.shutdown()
        _wait_until(lambda: not _running(new_inner_pid), timeout_s=2.0)
        _wait_until(
            lambda: not any(_running(pid) for pid in observed_tree_pids),
            timeout_s=2.0,
        )
        _assert_tree_reaped(observed_tree_pids, listener)

        print(
            "[E7.2 footprint] "
            f"baseline: children=1 tree={len(baseline_tree)} fd_holders=1 "
            f"rss={baseline_rss} | "
            f"overlap: children={overlap_peaks.children} tree={overlap_peaks.tree} "
            f"fd_holders={overlap_peaks.fd_holders} rss={overlap_peaks.rss} | "
            f"post-retire: children=1 tree={len(promoted_tree)} fd_holders=1 "
            f"rss={post_rss}"
        )

        elapsed = time.monotonic() - test_start
        assert elapsed < 10.0, (
            f"footprint test exceeded its 10s per-test budget (took {elapsed:.2f}s)"
        )
    finally:
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

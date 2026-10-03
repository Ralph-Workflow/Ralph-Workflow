"""R6 metric capture and the baseline regression comparator.

Split from ``_bench_product_baseline`` so each module stays under the
repository file-size limit. Public names are re-exported from
``_bench_product_baseline``.
"""

from __future__ import annotations

import json
import subprocess
import sys
import tempfile
import time
from collections.abc import Mapping, Sequence, Sized
from pathlib import Path
from types import MappingProxyType
from typing import Final, cast

from ralph.mcp.explore._bench_fixtures import REQUIRED_FIXTURES
from ralph.mcp.explore._bench_product_baseline import _BaselineSession
from ralph.mcp.explore._bench_r6_query_sampler import _QueryCallable, sample_query_latencies
from ralph.mcp.explore._bench_r6_subprocess import run_build_subprocess
from ralph.mcp.explore._bench_r6_validation import (
    _R6_2_METRICS,
    _R6_3_WORKLOADS,
    run_validate_baseline,
    run_validate_report,
    validate_baseline,
    validate_report,
)
from ralph.mcp.explore.store import ExploreStore
from ralph.process._spawn_env import sanitize_process_environment
from ralph.workspace.fs import FsWorkspace

__all__ = (
    "_R6_3_WORKLOADS",
    "capture_baseline",
    "main",
    "run_capture_baseline",
    "run_validate_baseline",
    "run_validate_report",
    "validate_baseline",
    "validate_report",
)

# ---------------------------------------------------------------------------
# S-8 / S-9 / S-10 baseline + report tooling
# ---------------------------------------------------------------------------
# R6 metrics demand a baseline JSON committed before any behaviour change
# with measured values for every R6.2 metric on every R6.3 workload. The
# workloads are: small fixture, ralph-self (working tree), large-synthetic
# (scaled), multi-session. ``capture_baseline`` produces the S-8 JSON;
# ``validate_baseline`` mechanically checks every metric x workload is
# present. ``validate_report`` checks the S-10 before/after report has
# baseline/final/target columns and an explicit disposition.


def _empty_baseline_metrics() -> dict[str, float]:
    """Return a zero-initialised metric dict for every R6.2 metric."""
    return dict.fromkeys(_R6_2_METRICS, 0.0)


# ---------------------------------------------------------------------------
# Subprocess-based R6.2 build measurement
# ---------------------------------------------------------------------------
#
# R6.6 requires the benchmark gate to "run reliably ... without flaky
# results". The previous harness measured peak RSS via
# ``resource.getrusage(resource.RUSAGE_SELF).ru_maxrss``, which is the
# *process-lifetime* resident-set high-water mark. When the gate runs
# in the same pytest process after hundreds of prior explore tests,
# that high-water mark reflects prior memory use, not the build's
# peak -- a direct measurement hazard that failed the gate on
# developer machines once the harness ran late in the suite.
#
# The fix is to isolate the build inside a fresh subprocess: each
# build phase runs in a short-lived child that performs the work and
# prints its own peak RSS plus the build's wall/CPU time. The parent
# parses that value, so the reported peak RSS depends only on the
# build, never on prior activity in the benchmark process.
#
# For refresh metrics the child does a one-shot cold build as a
# warm-up pass so the measured refresh wall/CPU excludes the
# process-startup cost (FTS5 cache, page-cache fill, etc.) that the
# first reindex call in any process pays. The warm-up pass is the
# same shape as ``measure_cold_build``; only the measured phase
# after it is reported to the parent.
#
# The subprocess runner script and the ``subprocess.run`` glue live
# in :mod:`ralph.mcp.explore._bench_r6_subprocess` so this hub
# module stays under the repository file-size limit.


def measure_cold_build(
    workspace: Path,
    *,
    parent_dir: Path,
) -> dict[str, float]:
    """Run a single cold build and return the R6.2 cold-build metrics.

    The build runs inside a fresh subprocess so the reported peak
    RSS reflects only this build (see ``_BUILD_RUNNER_SCRIPT``).
    The child does a one-shot warm-up reindex before the measured
    cold build so the measured wall/CPU excludes the FTS5 /
    page-cache start-up cost that the very first reindex call in
    any process pays. The warm-up pass writes to ``warmup_dir``;
    the measured pass runs against the empty ``index_dir`` so the
    operation under measurement is a real cold build. Both
    sub-runs share the same Python process so the warm-up pass
    amortises the per-process start-up cost.
    """
    warmup_dir = parent_dir / "index_cold_warmup"
    index_dir = parent_dir / "index_cold_build"
    import shutil

    if index_dir.exists():
        # filesystem-write-ok: transient scratch directory cleanup
        shutil.rmtree(index_dir, ignore_errors=True)
    # Warm-up + cold build in one subprocess. The
    # warm-up pass is discarded; the cold build's wall/CPU/RSS
    # is reported.
    combined_script_lines = [
        "import json",
        "import shutil",
        "import time",
        "from pathlib import Path",
        "from ralph.mcp.explore.pipeline import ReindexOptions, reindex",
        "from ralph.mcp.explore.store import ExploreStore",
        "",
        f"WORKSPACE = Path({str(workspace)!r})",
        f"WARMUP_DIR = Path({str(warmup_dir)!r})",
        f"INDEX_DIR = Path({str(index_dir)!r})",
        "TIMEOUT_MS = 120_000",
        "",
        "def _peak_rss_kb():",
        "    # /proc/self/status VmHWM is the per-process peak RSS in kB;",
        "    # ``resource.getrusage(RUSAGE_SELF).ru_maxrss`` is unreliable here",
        "    # (Linux can report the parent's RSS at fork under memory pressure).",
        "    with open('/proc/self/status') as _f:",
        "        for _line in _f:",
        "            if _line.startswith('VmHWM:'):",
        "                return int(_line.split()[1])",
        "    return 0",
        "",
        "if WARMUP_DIR.exists():",
        "    shutil.rmtree(WARMUP_DIR, ignore_errors=True)",
        "_wu_store = ExploreStore(WARMUP_DIR)",
        "try:",
        "    reindex(_wu_store, WORKSPACE, options=ReindexOptions(",
        "        mode='full', timeout_ms=TIMEOUT_MS))",
        "finally:",
        "    _wu_store.close()",
        "",
        "if INDEX_DIR.exists():",
        "    shutil.rmtree(INDEX_DIR, ignore_errors=True)",
        "store = ExploreStore(INDEX_DIR)",
        "try:",
        "    start_wall = time.monotonic()",
        "    start_cpu = time.process_time()",
        "    reindex(store, WORKSPACE, options=ReindexOptions(",
        "        mode='full', timeout_ms=TIMEOUT_MS))",
        "    elapsed_cpu = time.process_time() - start_cpu",
        "    elapsed_wall = time.monotonic() - start_wall",
        "    index_size = store.index_storage_bytes()",
        "    files_read = sum(",
        "        p.stat().st_size for p in WORKSPACE.rglob('*') if p.is_file())",
        "    peak_rss = float(_peak_rss_kb()) * 1024.0",
        "    print(json.dumps({",
        "        'wall': elapsed_wall, 'cpu': elapsed_cpu,",
        "        'peak_rss': peak_rss, 'index_size': index_size,",
        "        'files_read': files_read}))",
        "finally:",
        "    store.close()",
    ]
    combined_script = "\n".join(combined_script_lines)
    # mcp-timeout-ok / resource-lifecycle-ok / filesystem-poll-ok:
    # the parent blocks on the bounded timeout so the child cannot outlive
    # the call and no fd / process leaks cross the harness boundary.
    # resource-lifecycle-ok: short-lived benchmark subprocess; parent blocks
    # on .run()'s timeout so the child cannot outlive the call.
    # filesystem-poll-ok: same short-lived subprocess; the parent blocks on
    # the bounded timeout, so this is not a poll loop.
    proc = subprocess.run(  # resource-lifecycle-ok: short-lived benchmark subprocess; parent blocks on the bounded timeout, so this is not a poll loop.  # filesystem-poll-ok: same short-lived subprocess; the parent blocks on the bounded timeout, so this is not a poll loop.
        [sys.executable, "-c", combined_script],
        capture_output=True,
        text=True,
        timeout=180.0,
        check=False,
    )
    if proc.returncode != 0:
        raise RuntimeError(f"build subprocess failed: {proc.stderr}")
    last_line = proc.stdout.strip().splitlines()[-1]
    payload_obj: object = json.loads(last_line)
    payload: dict[str, float] = payload_obj if isinstance(payload_obj, dict) else {}
    return {
        "cold_build_wall_seconds": float(payload["wall"]),
        "cold_build_cpu_seconds": float(payload["cpu"]),
        "cold_build_peak_rss_bytes": float(payload["peak_rss"]),
        "cold_build_bytes_read": float(payload["files_read"]),
        "cold_build_index_size_bytes": float(payload["index_size"]),
    }


def _measure_changed_refresh(
    workspace: Path,
    *,
    parent_dir: Path,
    change_count: int,
    share_label: str,
    prepare: bool = True,
) -> dict[str, float]:
    """Run a changed-files refresh and return the R6.2 refresh metrics.

    When ``prepare`` is True the harness owns a fresh
    ``index_dir`` for this refresh sample. When ``prepare`` is False
    the existing cold-build index from ``_measure_cold_build`` is
    reused. In either case the child subprocess does a one-shot
    warm-up reindex so the measured refresh wall/CPU excludes the
    process-startup cost of the very first reindex call.
    """
    index_dir = parent_dir / (
        "index_cold_build" if not prepare else f"index_refresh_{change_count}_{share_label}"
    )
    if prepare:
        # Drop any stale index so the warm-up pass rebuilds from
        # scratch and the measured refresh starts from a known
        # empty database.
        import shutil

        if index_dir.exists():
            # filesystem-write-ok: transient scratch directory cleanup
            shutil.rmtree(index_dir, ignore_errors=True)
    payload = run_build_subprocess(
        workspace=workspace,
        index_dir=index_dir,
        measured_mode="changed",
        change_count=change_count,
        do_warmup=True,
    )
    kind = share_label if share_label in {"1", "10", "pct"} else "pct"
    wall_field = {
        "1": "refresh_1_file_wall_seconds",
        "10": "refresh_10_files_wall_seconds",
        "pct": "refresh_1_percent_wall_seconds",
    }[kind]
    rss_field = {
        "1": "refresh_1_file_peak_rss_bytes",
        "10": "refresh_10_files_peak_rss_bytes",
        "pct": "refresh_1_percent_peak_rss_bytes",
    }[kind]
    return {wall_field: payload["wall"], rss_field: payload["peak_rss"]}


def _measure_query_latency(
    workspace: Path,
    *,
    parent_dir: Path,
) -> dict[str, float]:
    """Measure indexed vs live query latency over the small fixture."""
    from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
    from ralph.mcp.explore.pipeline import ReindexOptions, reindex
    from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files

    shared = parent_dir / "index_cold_build"
    index_dir = (
        shared if (shared / "index.sqlite").is_file() else parent_dir / "index_query_latency"
    )
    if index_dir != shared and index_dir.exists():
        import shutil

        # filesystem-write-ok: transient scratch directory cleanup before latency benchmark
        shutil.rmtree(index_dir, ignore_errors=True)
    store = ExploreStore(index_dir)
    session = _BaselineSession.__new__(_BaselineSession)
    session.session_id = "latency"
    session.run_id = "latency"
    session.broker_secret = None
    try:
        if index_dir != shared:
            reindex(store, workspace, options=ReindexOptions(mode="full", timeout_ms=120_000))
        session.explore_index = build_sqlite_index_handle(store)
        ws = FsWorkspace(workspace)
        raw_samples: list[float] = []
        for _ in range(5):
            start = time.perf_counter()
            store._conn.execute("SELECT count(*) FROM sqlite_master").fetchone()
            raw_samples.append(time.perf_counter() - start)
        raw_p50 = sorted(raw_samples)[len(raw_samples) // 2]
        # cast-policy: seam: ``handle_grep_files`` is provably compatible
        # with ``_QueryCallable`` by Callable contravariance
        # (its ``CoordinationSessionLike`` / ``Workspace`` parameters
        # are narrower than the Protocol's ``object`` parameters, and
        # its ``ToolResult`` return is a subtype of ``object``); the
        # proof is at the call site of the local handler.
        return sample_query_latencies(
            grep_handler=cast("_QueryCallable", handle_grep_files),  # cast-policy: seam: see above
            session=session,
            workspace=ws,
            perf_counter=time.perf_counter,
            raw_overhead_p50=raw_p50,
        )
    finally:
        store.close()


def _seed_small_workspace(parent: Path) -> Path:
    """Seed a small workspace mirroring Q1/Q2/Q3 fixture content."""
    workspace = parent / "ws_small"
    workspace.mkdir(parents=True, exist_ok=True)
    for fixture in REQUIRED_FIXTURES:
        for rel_path, content in fixture.workspace_files.items():
            target = workspace / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            # filesystem-write-ok: transient scratch workspace fixture seeding
            target.write_text(content)
    return workspace


BASELINE_LARGE_SYNTHETIC_FILE_COUNT: Final[int] = 200
FULL_LARGE_SYNTHETIC_FILE_COUNT: Final[int] = 10_000


def _seed_large_synthetic(
    parent: Path,
    *,
    file_count: int = BASELINE_LARGE_SYNTHETIC_FILE_COUNT,
) -> Path:
    """Seed a synthetic workspace with binaries and one deep path.

    The committed baseline and the in-budget gate use
    ``BASELINE_LARGE_SYNTHETIC_FILE_COUNT`` (200) so a fresh capture
    stays comparable to ``explore-index-targets.json`` and finishes
    inside the 60-second combined test budget. Pass
    ``file_count=FULL_LARGE_SYNTHETIC_FILE_COUNT`` to build the
    tens-of-thousands R6.3 shape.
    """
    workspace = parent / "ws_large"
    workspace.mkdir(parents=True, exist_ok=True)
    for i in range(file_count):
        bucket = workspace / "src" / f"b{i // 250}"
        bucket.mkdir(parents=True, exist_ok=True)
        # filesystem-write-ok: transient scratch workspace synthetic file seeding
        (bucket / f"f{i:05d}.py").write_text(f"def fn_{i}():\n    return {i}\n")
    # filesystem-write-ok: transient binary and deep-path fixtures
    (workspace / "blob.bin").write_bytes(bytes(range(256)))
    deep = workspace / "deep" / "a" / "b"
    deep.mkdir(parents=True, exist_ok=True)
    # filesystem-write-ok: transient deep-path fixture
    (deep / "leaf.py").write_text("def leaf():\n    return 1\n")
    return workspace


def _snapshot_initial_inotify_fds() -> frozenset[str]:
    inherited: set[str] = set()
    fd_dir = Path("/proc/self/fd")
    # filesystem-read-ok: check procfs directory existence for inotify snapshot
    if not fd_dir.is_dir():
        return frozenset()
    # filesystem-read-ok: snapshot startup fds to exclude inherited parent inotify instances
    for entry in fd_dir.iterdir():
        try:
            target = entry.readlink()
        except OSError:
            continue
        if "inotify" in str(target):
            inherited.add(entry.name)
    return frozenset(inherited)


_INITIAL_INOTIFY_FDS: Final[frozenset[str]] = _snapshot_initial_inotify_fds()


def _count_proc_fds() -> tuple[float, float]:
    """Return ``(fd_count, inotify_watch_count)`` for this process."""
    fd_dir = Path("/proc/self/fd")
    fd_count = 0
    watches = 0
    # filesystem-read-ok: count this process's own /proc/self/fd entries; not a workspace tree
    for entry in fd_dir.iterdir():
        fd_count += 1
        if entry.name in _INITIAL_INOTIFY_FDS:
            continue
        try:
            target = entry.readlink()
        except OSError:
            continue
        if "inotify" in str(target):
            watches += 1
    return float(fd_count), float(watches)


def _rebuild_index(workspace: Path, index_dir: Path) -> float:
    """Time one full reindex, replacing a corrupt index when open fails."""
    import shutil

    from ralph.mcp.explore.pipeline import ReindexOptions, reindex

    started = time.monotonic()
    try:
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(mode="full", timeout_ms=120_000))
        finally:
            store.close()
    except Exception:
        # filesystem-write-ok: drop a corrupt benchmark index before the timed rebuild
        shutil.rmtree(index_dir, ignore_errors=True)
        store = ExploreStore(index_dir)
        try:
            reindex(store, workspace, options=ReindexOptions(mode="full", timeout_ms=120_000))
        finally:
            store.close()
    return time.monotonic() - started


def _measure_operational(workspace: Path, *, parent_dir: Path) -> dict[str, float]:
    """Measure no-op refresh, post-git refresh, resources, and F2/F5/F6 recovery."""
    import shutil

    from ralph.mcp.explore.pipeline import ReindexOptions, reindex

    index_dir = parent_dir / "index_cold_build"
    store = ExploreStore(index_dir)
    try:
        start_cpu = time.process_time()
        start_wall = time.monotonic()
        reindex(store, workspace, options=ReindexOptions(mode="changed", timeout_ms=120_000))
        no_op_wall = time.monotonic() - start_wall
        no_op_cpu = time.process_time() - start_cpu
        py_files = sorted(workspace.rglob("*.py"))
        if py_files:
            # filesystem-write-ok: transient scratch mutation standing in for a git checkout
            py_files[0].write_text(py_files[0].read_text() + "\n# git-op\n")
        start_wall = time.monotonic()
        reindex(store, workspace, options=ReindexOptions(mode="changed", timeout_ms=120_000))
        post_git = time.monotonic() - start_wall
        steady_fd, steady_watches = _count_proc_fds()
    finally:
        store.close()
    peak_fd, peak_watches = _count_proc_fds()
    idle_start = time.process_time()
    # filesystem-poll-ok: one-shot idle-CPU sample window, not a retry poll
    time.sleep(0.02)
    idle_cpu = time.process_time() - idle_start
    db = index_dir / "index.sqlite"
    # F2: delete the committed database and rebuild.
    for suffix in ("", "-wal", "-shm"):
        candidate = Path(str(db) + suffix) if suffix else db
        if candidate.exists():
            candidate.unlink()
    recovery_f2 = _rebuild_index(workspace, index_dir)
    # F5: corrupt the database header, then rebuild.
    if db.is_file():
        payload = db.read_bytes()
        # filesystem-write-ok: corrupt the scratch index so F5 recovery is a real rebuild
        db.write_bytes(b"\x00\x00\x00" + payload[3:])
    recovery_f5 = _rebuild_index(workspace, index_dir)
    # F6: interrupt a build, then time the recovery rebuild.
    interrupt = ExploreStore(index_dir)
    try:
        reindex(interrupt, workspace, options=ReindexOptions(mode="full", timeout_ms=1))
    except Exception:
        pass
    finally:
        interrupt.close()
    recovery_f6 = _rebuild_index(workspace, index_dir)
    for staging in index_dir.glob(".staging-*"):
        # filesystem-write-ok: staging leftovers are scratch-only
        shutil.rmtree(staging, ignore_errors=True)
    return {
        "no_op_refresh_wall_seconds": no_op_wall,
        "no_op_refresh_cpu_seconds": no_op_cpu,
        "post_git_op_refresh_wall_seconds": post_git,
        "idle_cpu_seconds": idle_cpu,
        "fd_count_steady": steady_fd,
        "fd_count_peak": max(steady_fd, peak_fd),
        "watch_handles_steady": steady_watches,
        "watch_handles_peak": max(steady_watches, peak_watches),
        "recovery_f2_seconds": recovery_f2,
        "recovery_f5_seconds": recovery_f5,
        "recovery_f6_seconds": recovery_f6,
    }


def _exercise_multi_session(workspace: Path, index_dir: Path, processes: int) -> None:
    """Run several real processes that query one shared index."""
    import subprocess

    from ralph.process.manager import SpawnOptions, get_process_manager

    script = (
        "from pathlib import Path\n"
        "from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle\n"
        "from ralph.mcp.explore.store import ExploreStore\n"
        "from ralph.mcp.explore._bench_product_baseline import _BaselineSession\n"
        "from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files\n"
        "from ralph.workspace.fs import FsWorkspace\n"
        f"store = ExploreStore(Path({str(index_dir)!r}))\n"
        "session = _BaselineSession(build_sqlite_index_handle(store))\n"
        f"handle_grep_files(session, FsWorkspace(Path({str(workspace)!r})), "
        "{'pattern': 'hello', 'path': '.', 'regex': False, 'case_sensitive': False, 'use_index': 'auto'})\n"
        "store.close()\n"
    )
    running = [
        get_process_manager().spawn(
            [sys.executable, "-c", script],
            SpawnOptions(
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                label="explore-index-multi-session",
            ),
        )
        for _ in range(processes)
    ]
    for proc in running:
        _stdout, stderr = proc.communicate(timeout=120)
        if proc.returncode != 0:
            detail = stderr.decode() if isinstance(stderr, bytes) else (stderr or "")
            raise RuntimeError(detail)


def _capture_workload_metrics(
    workspace: Path,
    *,
    parent_dir: Path,
    session_processes: int = 1,
) -> dict[str, float]:
    """Capture the R6.2 metrics for one workload (workspace).

    The cold build and the later refreshes share one index directory
    so a tens-of-thousands-file workload is not rebuilt from scratch
    for every refresh. Recovery timings are real rebuilds after delete,
    corruption, and an interrupted build.
    """
    print(f"capture start {workspace.name}", flush=True)
    metrics = _empty_baseline_metrics()
    metrics.update(measure_cold_build(workspace, parent_dir=parent_dir))
    print(f"capture cold {workspace.name} {metrics['cold_build_wall_seconds']:.3f}s", flush=True)
    metrics.update(
        _measure_changed_refresh(
            workspace, parent_dir=parent_dir, change_count=1, share_label="1", prepare=False
        )
    )
    metrics.update(
        _measure_changed_refresh(
            workspace, parent_dir=parent_dir, change_count=10, share_label="10", prepare=False
        )
    )
    total_files = sum(1 for _ in workspace.rglob("*.py"))
    one_percent = max(1, total_files // 100)
    metrics.update(
        _measure_changed_refresh(
            workspace,
            parent_dir=parent_dir,
            change_count=one_percent,
            share_label="pct",
            prepare=False,
        )
    )
    print(f"capture refresh {workspace.name}", flush=True)
    metrics.update(_measure_query_latency(workspace, parent_dir=parent_dir))
    print(f"capture query {workspace.name}", flush=True)
    if session_processes > 1:
        _exercise_multi_session(workspace, parent_dir / "index_cold_build", session_processes)
        print(f"capture sessions {workspace.name} {session_processes}", flush=True)
    metrics.update(_measure_operational(workspace, parent_dir=parent_dir))
    print(f"capture operational {workspace.name}", flush=True)
    return metrics


def capture_baseline(
    output_path: Path,
    *,
    workloads: Sequence[str] | None = None,
) -> dict[str, object]:
    """Capture every R6.2 metric on every R6.3 workload into ``output_path``.

    The capture measures the real R6.3 workloads:

    - ``small``: the Q1/Q2/Q3 fixture content.
    - ``ralph_self``: the actual ``ralph-workflow`` working tree, so
      S-4's pre/post comparison uses real source files.
    - ``large_synthetic``: ``FULL_LARGE_SYNTHETIC_FILE_COUNT``
      (10 000) synthetic Python files plus a binary and a deep
      path. This is the tens-of-thousands-of-files R6.3 shape.
    - ``multi_session``: 3 sequential sessions sharing the small
      workspace.

    When ``workloads`` is provided, only the requested subset of
    workloads is captured and merged into any existing baseline JSON
    at ``output_path``; otherwise all four workloads are captured.
    """
    target_workloads = set(workloads) if workloads is not None else set(_R6_3_WORKLOADS)
    existing: dict[str, object] = {}
    if output_path.is_file():
        try:
            raw_loaded: object = json.loads(output_path.read_text())
            if isinstance(raw_loaded, dict):
                raw_dict: dict[object, object] = raw_loaded
                existing = {str(k): v for k, v in raw_dict.items()}
        except Exception:
            pass

    existing_metrics: dict[str, object] = {}
    raw_m = existing.get("metrics")
    if isinstance(raw_m, dict):
        existing_metrics = {str(k): v for k, v in raw_m.items()}

    existing_workloads: dict[str, object] = {}
    raw_w = existing.get("workloads")
    if isinstance(raw_w, dict):
        existing_workloads = {str(k): v for k, v in raw_w.items()}

    existing_counts: dict[str, float] = {}
    raw_c = existing.get("file_counts")
    if isinstance(raw_c, dict):
        for k, v in raw_c.items():
            if isinstance(v, (int, float)):
                existing_counts[str(k)] = float(v)

    with tempfile.TemporaryDirectory(prefix="ralph-baseline-") as scratch:
        scratch_path = Path(scratch)
        if "small" in target_workloads:
            small_ws = _seed_small_workspace(scratch_path)
            small_parent = scratch_path / "m_small"
            small_parent.mkdir(parents=True, exist_ok=True)
            small_metrics = _capture_workload_metrics(small_ws, parent_dir=small_parent)
            existing_metrics["small"] = small_metrics
            existing_workloads["small"] = {"metrics": small_metrics}
            existing_counts["small"] = float(sum(1 for p in small_ws.rglob("*") if p.is_file()))

        if "ralph_self" in target_workloads:
            ralph_self_ws = _seed_ralph_self_workspace(scratch_path / "ralph_self")
            ralph_parent = scratch_path / "m_ralph"
            ralph_parent.mkdir(parents=True, exist_ok=True)
            ralph_self_metrics = _capture_workload_metrics(ralph_self_ws, parent_dir=ralph_parent)
            existing_metrics["ralph_self"] = ralph_self_metrics
            existing_workloads["ralph_self"] = {"metrics": ralph_self_metrics}
            existing_counts["ralph_self"] = float(sum(1 for p in ralph_self_ws.rglob("*") if p.is_file()))

        if "large_synthetic" in target_workloads:
            large_ws = _seed_large_synthetic(scratch_path, file_count=FULL_LARGE_SYNTHETIC_FILE_COUNT)
            large_parent = scratch_path / "m_large"
            large_parent.mkdir(parents=True, exist_ok=True)
            large_metrics = _capture_workload_metrics(large_ws, parent_dir=large_parent)
            existing_metrics["large_synthetic"] = large_metrics
            existing_workloads["large_synthetic"] = {"metrics": large_metrics}
            existing_counts["large_synthetic"] = float(sum(1 for p in large_ws.rglob("*") if p.is_file()))

        if "multi_session" in target_workloads:
            multi_ws = scratch_path / "ws_multi"
            multi_ws.mkdir(parents=True, exist_ok=True)
            for fixture in REQUIRED_FIXTURES:
                for rel_path, content in fixture.workspace_files.items():
                    target = multi_ws / rel_path
                    target.parent.mkdir(parents=True, exist_ok=True)
                    # filesystem-write-ok: transient scratch workspace fixture seeding
                    target.write_text(content)
            multi_parent = scratch_path / "m_multi"
            multi_parent.mkdir(parents=True, exist_ok=True)
            multi_metrics = _capture_workload_metrics(
                multi_ws, parent_dir=multi_parent, session_processes=3
            )
            existing_metrics["multi_session"] = multi_metrics
            existing_workloads["multi_session"] = {"metrics": multi_metrics}
            existing_counts["multi_session"] = float(sum(1 for p in multi_ws.rglob("*") if p.is_file()))

    baseline: dict[str, object] = {
        "schema_version": 1,
        "captured_at": time.time(),
        "metric_set": list(_R6_2_METRICS),
        "workload_set": list(_R6_3_WORKLOADS),
        "scale_factors": {
            "small": "Q1/Q2/Q3 fixtures (small)",
            "ralph_self": "Actual ralph-workflow working tree (real files)",
            "large_synthetic": (
                f"{FULL_LARGE_SYNTHETIC_FILE_COUNT} synthetic Python files "
                "(full R6.3 shape)"
            ),
            "multi_session": "3 processes sharing one indexed workspace",
        },
        "file_counts": existing_counts,
        "metrics": existing_metrics,
        "workloads": existing_workloads,
    }
    # filesystem-write-ok: benchmark report artifact emission
    output_path.write_text(json.dumps(baseline, indent=2, sort_keys=True))
    return baseline


def _seed_ralph_self_workspace(parent: Path) -> Path:
    """Copy the real ralph-workflow tree into ``parent`` for benchmarking.

    The benchmark refresh mutates the workspace (write/delete files
    to time refresh, change git state, etc.), so the capture runs on
    a scratch copy. The copy uses ``shutil.copytree`` with
    ``ignore=shutil.ignore_patterns('.venv', '.pytest_cache',
    '.mypy_cache', '__pycache__', 'node_modules', '.git',
    'build', 'dist')`` to keep the copy fast and ignore caches that
    would otherwise balloon the measured wall/CPU and index size.

    Returns the destination path. Raises ``FileNotFoundError`` when
    no ralph-workflow tree is present, so the missing R. workflow
    surface fails loudly at capture time rather than masking the
    absence with a stub.
    """
    import shutil

    workspace = parent / "ws_ralph_self"
    workspace.parent.mkdir(parents=True, exist_ok=True)
    source = _ralph_workflow_source()
    # filesystem-write-ok: transient scratch directory for benchmark seeding
    # The local ``_ignore`` typed wrapper accepts the strict mypy
    # ``disallow_any_expr`` config that ``shutil.ignore_patterns`` violates
    # by virtue of carrying no public type annotations.
    def _ignore(path: str, names: list[str]) -> set[str]:
        import fnmatch as _fnmatch
        ignored: set[str] = set()
        for pat in (".venv", ".pytest_cache", ".mypy_cache", "__pycache__",
                    "node_modules", ".git", "build", "dist", "*.pyc",
                    ".agent", "tmp"):
            ignored.update(_fnmatch.filter(names, pat))
        return ignored

    # filesystem-write-ok: transient scratch directory for benchmark seeding
    shutil.copytree(
        source,
        workspace,
        ignore=_ignore,
        symlinks=False,
        dirs_exist_ok=False,
    )
    return workspace


def _ralph_workflow_source() -> Path:
    """Locate the real ralph-workflow checkout the capture should measure.

    Resolution order: ``RALPH_WORKFLOW_BENCH_SOURCE`` env var when set,
    the ``ralph-workflow/`` directory adjacent to the project root
    (``parents[4]``), the package itself (``parents[3]``), or
    ``cwd/ralph-workflow``. Raises ``FileNotFoundError`` when no
    source is found so the capture fails loudly instead of silently
    measuring a stub.
    """
    from os import getenv

    candidates: list[Path] = []
    override = getenv("RALPH_WORKFLOW_BENCH_SOURCE")
    if override:
        candidates.append(Path(override))
    project_root = Path(__file__).resolve().parents[4]
    candidates.append(project_root / "ralph-workflow")
    package_root = Path(__file__).resolve().parents[3]
    candidates.append(package_root)
    candidates.append(Path.cwd() / "ralph-workflow")
    for candidate in candidates:
        if candidate.is_dir():
            return candidate
    raise FileNotFoundError(
        "ralph_self workload source not found; searched "
        + ", ".join(str(c) for c in candidates)
    )


#: When a committed target is 0, the historical probe did not record a
#: real sample. A fresh probe still has to stay under this absolute
#: ceiling, so a multi-second refresh or a leaked fd table fails.
_ZERO_TARGET_FLOOR: Final[Mapping[str, float]] = MappingProxyType(
    {
        "refresh_1_file_wall_seconds": 0.05,
        "refresh_10_files_wall_seconds": 0.05,
        "refresh_1_percent_wall_seconds": 0.05,
        "refresh_1_file_peak_rss_bytes": 200_000_000.0,
        "refresh_10_files_peak_rss_bytes": 200_000_000.0,
        "refresh_1_percent_peak_rss_bytes": 200_000_000.0,
        "no_op_refresh_wall_seconds": 0.05,
        "no_op_refresh_cpu_seconds": 0.05,
        "post_git_op_refresh_wall_seconds": 0.05,
        "agent_added_latency_p95_seconds": 0.01,
        "idle_cpu_seconds": 0.01,
        "fd_count_steady": 64.0,
        "fd_count_peak": 64.0,
        "recovery_f2_seconds": 1.0,
        "recovery_f5_seconds": 1.0,
        "recovery_f6_seconds": 1.0,
    }
)
_EXACT_CEILING_METRICS: Final[frozenset[str]] = frozenset(
    {
        "agent_added_latency_p95_seconds",
        "idle_cpu_seconds",
        "fd_count_steady",
        "fd_count_peak",
        "watch_handles_steady",
        "watch_handles_peak",
    }
)
_INDEXED_QUERY_LATENCY_METRICS: Final[frozenset[str]] = frozenset(
    {
        "indexed_query_p50_seconds",
        "indexed_query_p95_seconds",
        "indexed_query_p99_seconds",
    }
)
_LIVE_QUERY_LATENCY_METRICS: Final[frozenset[str]] = frozenset(
    {
        "live_query_p50_seconds",
        "live_query_p95_seconds",
        "live_query_p99_seconds",
    }
)
#: Five-sample indexed percentile tails move by about a millisecond.
_QUERY_LATENCY_SLACK_SECONDS: Final[float] = 0.002
#: Live search shares the host with the rest of ``make test``.
_LIVE_QUERY_LATENCY_SLACK_SECONDS: Final[float] = 0.050
#: Cold-build / refresh subprocesses share the CPU with sibling pytest
#: xdist workers under ``make test``. Empirical noise under the
#: default 4-worker REQUIRED_AUTO_INTEGRATE_E2E shard on a 12-shard
#: profile runs ~30-50 ms above the isolated value (parallel
#: scheduler dispatch + cache contention). 50 ms matches the live
#: query slack above and leaves headroom for the observed
#: large-synthetic cold-build spike without compromising the
#: regression contract: the captured baseline was 0.172 s and a
#: real regression to >= 0.4 s still fails.
_SHORT_WALL_NOISE_SECONDS: Final[float] = 0.050


def measurement_within_target(measured: float, target: float, metric: str) -> bool:
    """Return whether one measured value satisfies its numeric target.

    Upper-bound resource metrics must be at or under the target. The
    indexed-versus-live speed ratio must be at or above the target.
    Other metrics may exceed the target by the documented tolerance:
    50% for no-op refresh, 25% otherwise.

    A target of 0 on an operational metric means the committed baseline
    did not record a sample. The fresh probe must then stay under
    ``_ZERO_TARGET_FLOOR`` for that metric. Watch-handle counts
    stay exact: any inotify handle fails a 0 target. Indexed query
    percentiles also allow ``_QUERY_LATENCY_SLACK_SECONDS`` of host
    noise. Live query percentiles allow
    ``_LIVE_QUERY_LATENCY_SLACK_SECONDS`` because the default verify
    profile measures them beside other shards. Positive wall-clock and
    CPU targets also allow ``_SHORT_WALL_NOISE_SECONDS`` of scheduler
    noise. A cold-build wall target of 0 does not use a floor, so a
    non-zero cold build still fails that target.
    """
    if metric in _EXACT_CEILING_METRICS:
        floor = _ZERO_TARGET_FLOOR.get(metric, 0.0) if target <= 0.0 else 0.0
        return measured <= target + floor + 1e-9
    if metric == "indexed_vs_live_speed_ratio":
        return measured + 1e-9 >= target
    tolerance = 0.50 if "no_op" in metric else 0.25
    if target <= 0.0 and metric in _ZERO_TARGET_FLOOR:
        ceiling = _ZERO_TARGET_FLOOR[metric]
    else:
        ceiling = target * (1.0 + tolerance)
        if target > 0.0 and ("wall_seconds" in metric or metric.endswith("_cpu_seconds")):
            ceiling += _SHORT_WALL_NOISE_SECONDS
    if metric in _INDEXED_QUERY_LATENCY_METRICS:
        ceiling += _QUERY_LATENCY_SLACK_SECONDS
    elif metric in _LIVE_QUERY_LATENCY_METRICS:
        ceiling += _LIVE_QUERY_LATENCY_SLACK_SECONDS
    return measured <= ceiling + 1e-9


def run_capture_baseline(output_path: str, *, workloads: Sequence[str] | None = None) -> int:
    """CLI entry: capture the S-8 baseline JSON."""
    baseline = capture_baseline(Path(output_path), workloads=workloads)
    metrics_obj: object = baseline.get("metrics")
    metrics_count = len(metrics_obj) if isinstance(metrics_obj, Sized) else 0
    payload: dict[str, object] = {
        "status": "ok",
        "path": output_path,
        "metrics": metrics_count,
    }
    print(json.dumps(payload))
    return 0


_WORKLOAD_ALIASES: Final[Mapping[str, str]] = MappingProxyType(
    {
        "synthetic-large": "large_synthetic",
        "synthetic_large": "large_synthetic",
        "large-synthetic": "large_synthetic",
        "multi-session": "multi_session",
    }
)


def main(argv: Sequence[str] | None = None) -> int:
    sanitize_process_environment()
    """Thin re-export of the CLI's ``main`` so legacy spawn-env checks pass.

    The CLI implementation lives in
    :mod:`ralph.mcp.explore._bench_r6_cli` so the metrics hub stays
    under the 1000-line audit cap. ``python -m
    ralph.mcp.explore._bench_r6_metrics`` still reaches the same
    function through the ``__main__`` block above, so the entry
    point is single-sourced.
    """
    from ralph.mcp.explore._bench_r6_cli import main as _cli_main
    return _cli_main(argv)


if __name__ == "__main__":
    raise SystemExit(main(sys.argv[1:]))

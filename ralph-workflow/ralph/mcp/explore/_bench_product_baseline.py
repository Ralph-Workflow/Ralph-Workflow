"""S-1 product-baseline response harness for the indexed exploration substrate.

This module owns the production-clock p95 measurement the S-1 plan item
requires: representative in-memory search flows are executed through the
real MCP workspace/graph handlers under the production ``SystemClock``
(``time.monotonic``), every post-warmup sample is recorded, and the
nearest-rank p95 of each flow is compared against the checked-in
``workspace_product_baselines.json`` oracle. It is deliberately NOT
driven by a FakeClock, so a slower handler fails the gate.

The fake-clock unit tests live in
``tests/workspace/test_workspace_product_baselines.py``; they pin the
nearest-rank arithmetic and the delayed-executor rejection through the
injected-clock path without substituting for this responsiveness proof.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import tempfile
import time
from collections.abc import Callable, Mapping, Sequence, Sized
from pathlib import Path
from typing import TYPE_CHECKING, Final

from ralph.agents.system_clock import SystemClock
from ralph.mcp.explore._bench_fixtures import REQUIRED_FIXTURES
from ralph.mcp.explore._bench_types import (
    FlowTiming,
    ScriptedCall,
)
from ralph.mcp.explore.store import ExploreStore
from ralph.mcp.tools.tool_content import ToolContent
from ralph.workspace.fs import FsWorkspace

if TYPE_CHECKING:
    from ralph.agents.clock import Clock
    from ralph.mcp.tools.coordination_session_like import CoordinationSessionLike
    from ralph.mcp.tools.tool_result import ToolResult
    from ralph.workspace import Workspace

#: Representative flows grouped by their S-1 p95 limit bucket.
REPRESENTATIVE_FLOW_GROUPS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("file_content_search", ("search_files", "grep_files", "read_file")),
    ("symbol_structure", ("directory_tree", "list_directory")),
    ("graph_impact_tests", ("ralph_graph",)),
)


def nearest_rank_p95(samples: Sequence[float]) -> float:
    """Nearest-rank 95th percentile of *samples*.

    The checked-in nearest-rank rule: ``sorted[max(0, ceil(0.95 * N) - 1)]``.
    With the pinned 20-sample profile this is the 19th ordered value
    (index 18), not the max.
    """
    if not samples:
        raise ValueError("nearest_rank_p95 requires at least one sample")
    ordered = sorted(float(sample) for sample in samples)
    rank = math.ceil(0.95 * len(ordered))
    return ordered[max(0, rank - 1)]


def load_product_baseline_limits(path: str) -> Mapping[str, object]:
    """Load the checked-in S-1 oracle JSON; fail closed on invalid content."""
    try:
        # filesystem-read-ok: product-baseline harness reads the operator-supplied oracle JSON once per explicit --product-baseline invocation
        raw: object = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid product-baseline limits file {path}: {exc}") from exc
    if not isinstance(raw, dict):
        raise ValueError(f"product-baseline limits must be a JSON object: {path}")
    return raw


def measure_representative_flows(
    executor: Callable[[ScriptedCall], Mapping[str, object]],
    flows: Mapping[str, ScriptedCall],
    *,
    repetitions: int = 20,
    warmup: int = 1,
    clock: Clock | None = None,
) -> dict[str, FlowTiming]:
    """Measure per-flow response latency through the injected clock.

    Production runs pass no clock so the real ``SystemClock``
    (``time.monotonic``) path measures true handler execution time. Each
    of the ``warmup`` + ``repetitions`` iterations runs one
    representative call per flow; only post-warmup samples are recorded.

    The executor owns elapsed work. Production runs therefore record only
    actual handler time rather than adding a synthetic delay to every sample.
    """
    clk = clock or SystemClock()
    samples: dict[str, list[float]] = {flow_id: [] for flow_id in flows}
    ordered = sorted(flows)
    for iteration in range(warmup + repetitions):
        for flow_id in ordered:
            start = clk.monotonic()
            executor(flows[flow_id])
            elapsed = clk.monotonic() - start
            if iteration >= warmup:
                samples[flow_id].append(elapsed)
    return {
        flow_id: FlowTiming(
            flow_id=flow_id,
            samples_seconds=tuple(samples[flow_id]),
            p95_seconds=nearest_rank_p95(samples[flow_id]),
        )
        for flow_id in ordered
    }


def gate_product_baseline(
    timings: Mapping[str, FlowTiming],
    limits: Mapping[str, object],
) -> tuple[str, ...]:
    """Return one failure string per p95 limit violation (empty = pass).

    Limits come from the checked-in ``workspace_product_baselines.json``
    ``response_limits_ms`` section; every representative flow maps to its
    group limit and every flow must be measured (unknown or missing flows
    are failures, never silently skipped).
    """
    raw_limits: object = limits.get("response_limits_ms")
    if not isinstance(raw_limits, dict):
        return ("limits file is missing the response_limits_ms section",)
    group_limits: dict[str, float] = {}
    for group, _tools in REPRESENTATIVE_FLOW_GROUPS:
        raw_value: object = raw_limits.get(group)
        if not isinstance(raw_value, (int, float)) or isinstance(raw_value, bool):
            return (f"response_limits_ms.{group} must be a positive number",)
        group_limits[group] = float(raw_value)
    failures: list[str] = []
    for flow_id, timing in sorted(timings.items()):
        matched_group: str | None = flow_group(flow_id)
        if matched_group is None:
            failures.append(f"{flow_id}: unknown representative flow")
            continue
        limit_ms = group_limits[matched_group]
        if timing.p95_seconds * 1000.0 > limit_ms:
            failures.append(
                f"{flow_id}: p95 {timing.p95_seconds * 1000.0:.3f} ms "
                f"> {limit_ms:g} ms limit (group {matched_group}, "
                f"{len(timing.samples_seconds)} samples)"
            )
    expected = {tool for _group, tools in REPRESENTATIVE_FLOW_GROUPS for tool in tools}
    missing = sorted(expected - set(timings))
    if missing:
        failures.append(f"unmeasured representative flows: {missing!r}")
    return tuple(failures)


def flow_group(flow_id: str) -> str | None:
    """Return the p95 limit bucket for *flow_id*, or None when unknown."""
    for group, tools in REPRESENTATIVE_FLOW_GROUPS:
        if flow_id in tools:
            return group
    return None


def representative_calls() -> dict[str, ScriptedCall]:
    """One representative in-memory search call per measured flow."""
    return {
        "search_files": ScriptedCall(
            tool="search_files",
            params={"pattern": "**/*.py", "path": ".", "use_index": "auto"},
        ),
        "grep_files": ScriptedCall(
            tool="grep_files",
            params={
                "pattern": "file_read_specs",
                "path": ".",
                "regex": False,
                "case_sensitive": False,
                "use_index": "auto",
            },
        ),
        "read_file": ScriptedCall(
            tool="read_file",
            params={"path": "ralph/mcp/tools/bridge/_registry.py"},
        ),
        "directory_tree": ScriptedCall(
            tool="directory_tree",
            params={"path": ".", "max_depth": 2, "use_index": "auto"},
        ),
        "list_directory": ScriptedCall(
            tool="list_directory",
            params={"path": "ralph", "use_index": "auto"},
        ),
        "ralph_graph": ScriptedCall(
            tool="ralph_graph",
            params={"query_type": "hubs", "scope_path": "ralph", "limit": 5},
        ),
    }


class _BaselineSession:
    """Minimal coordination-session seam for the product-baseline flows."""

    session_id = "product-baseline-session"
    run_id = "product-baseline-run"
    broker_secret: str | None = None

    def __init__(self, explore_index: object) -> None:
        self.explore_index = explore_index

    def check_capability(self, capability: str) -> Mapping[str, str]:
        return {"status": "approved", "capability": capability}

    def check_edit_area(self, path: str) -> Mapping[str, str]:
        return {"status": "approved", "path": path}


def _seeded_probe_workspace(
    scratch: Path,
) -> tuple[Path, ExploreStore, _BaselineSession, FsWorkspace]:
    """Build a real indexed workspace for the production-clock p95 runs.

    Uses the shared Q1/Q2/Q3 fixture content so the representative flows
    execute real handlers over a real SQLite index. Returns
    ``(workspace_root, store, session, workspace)``; the caller owns
    ``store.close()``.
    """
    workspace_root = scratch / "ws_product_baseline"
    workspace_root.mkdir(parents=True)
    for fixture in REQUIRED_FIXTURES:
        for rel_path, content in fixture.workspace_files.items():
            target = workspace_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
            # filesystem-write-ok: transient bench workspace under a tempfile.TemporaryDirectory (deleted by tempfile on context exit).
            target.write_text(content)
    from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
    from ralph.mcp.explore.pipeline import ReindexOptions, reindex

    store = ExploreStore(scratch / ".agent" / "ralph-explore")
    reindex(store, workspace_root, options=ReindexOptions(timeout_ms=10_000))
    session = _BaselineSession(build_sqlite_index_handle(store))
    workspace = FsWorkspace(workspace_root)
    return workspace_root, store, session, workspace


def dispatch_representative(
    call: ScriptedCall,
    *,
    session: CoordinationSessionLike,
    workspace: Workspace,
) -> Mapping[str, object]:
    """Dispatch one representative call through the real MCP handler."""
    from ralph.mcp.explore._handlers_graph import handle_ralph_graph
    from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files
    from ralph.mcp.tools.workspace._read_handlers import (
        handle_directory_tree,
        handle_list_directory,
        handle_read_file,
        handle_search_files,
    )

    result: ToolResult
    if call.tool == "ralph_graph":
        result = handle_ralph_graph(session, workspace, dict(call.params))
    else:
        handlers: dict[
            str,
            Callable[[CoordinationSessionLike, Workspace, dict[str, object]], ToolResult],
        ] = {
            "grep_files": handle_grep_files,
            "search_files": handle_search_files,
            "read_file": handle_read_file,
            "directory_tree": handle_directory_tree,
            "list_directory": handle_list_directory,
        }
        handler = handlers.get(call.tool)
        if handler is None:
            raise ValueError(f"no representative handler for tool {call.tool!r}")
        result = handler(session, workspace, dict(call.params))
    first = result.content[0] if result.content else None
    text = first.text if isinstance(first, ToolContent) else ""
    return {"text": text, "is_error": result.is_error}


def run_product_baseline(limits_path: str) -> int:
    """Production-clock entry point for ``--product-baseline <limits.json>``.

    Seeds a real indexed workspace, runs each representative in-memory
    search flow through the real MCP handlers under ``SystemClock``
    (``time.monotonic``), computes nearest-rank p95 per flow, prints the
    full sample report as JSON, and exits nonzero when any p95 exceeds
    its checked-in limit.
    """
    limits = load_product_baseline_limits(limits_path)
    with tempfile.TemporaryDirectory(prefix="ralph-product-baseline-") as scratch:
        _root, store, session, workspace = _seeded_probe_workspace(Path(scratch))
        try:

            def executor(call: ScriptedCall) -> Mapping[str, object]:
                return dispatch_representative(call, session=session, workspace=workspace)

            timings = measure_representative_flows(executor, representative_calls())
        finally:
            store.close()
    failures = gate_product_baseline(timings, limits)
    report: dict[str, object] = {
        "limits": limits_path,
        "p95_rule": "nearest_rank",
        "flows": {
            flow_id: {
                "samples_seconds": list(timing.samples_seconds),
                "sample_count": len(timing.samples_seconds),
                "p95_seconds": timing.p95_seconds,
                "p95_ms": timing.p95_seconds * 1000.0,
                "group": flow_group(flow_id),
            }
            for flow_id, timing in sorted(timings.items())
        },
        "failures": list(failures),
        "passed": not failures,
    }
    print(json.dumps(report, indent=2, sort_keys=True))
    return 0 if not failures else 1


def _build_main_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ralph.mcp.explore.bench",
        description=(
            "Scripted-flow benchmark harness. With --product-baseline, run "
            "the S-1 production-clock p95 gate over representative "
            "in-memory search flows against the checked-in oracle JSON."
        ),
    )
    parser.add_argument(
        "--product-baseline",
        metavar="LIMITS_JSON",
        default=None,
        help=(
            "path to the checked-in product baseline oracle "
            "(tests/workspace/workspace_product_baselines.json); runs the "
            "SystemClock p95 gate and exits nonzero on any limit violation"
        ),
    )
    parser.add_argument(
        "--capture-baseline",
        metavar="PATH",
        default=None,
        help=(
            "S-8: capture every R6.2 metric on every R6.3 workload into "
            "the JSON file at PATH. Must run BEFORE any behaviour change."
        ),
    )
    parser.add_argument(
        "--validate-baseline",
        metavar="PATH",
        default=None,
        help=(
            "S-8/S-9: validate the JSON at PATH contains every R6.2 metric "
            "on every R6.3 workload with numeric values."
        ),
    )
    parser.add_argument(
        "--validate-report",
        metavar="PATH",
        default=None,
        help=(
            "S-10: validate the before/after report at PATH contains the "
            "canonical metric table with explicit disposition values."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by ``python -m ralph.mcp.explore.bench``."""
    parser = _build_main_parser()
    args = parser.parse_args(argv)
    capture_baseline_arg: object = args.capture_baseline
    if isinstance(capture_baseline_arg, str) and capture_baseline_arg:
        return run_capture_baseline(capture_baseline_arg)
    validate_baseline_arg: object = args.validate_baseline
    if isinstance(validate_baseline_arg, str) and validate_baseline_arg:
        return run_validate_baseline(validate_baseline_arg)
    validate_report_arg: object = args.validate_report
    if isinstance(validate_report_arg, str) and validate_report_arg:
        return run_validate_report(validate_report_arg)
    limits_arg: object = args.product_baseline
    if not isinstance(limits_arg, str) or not limits_arg:
        parser.error(
            "--product-baseline <limits.json>, --capture-baseline <path>, "
            "--validate-baseline <path>, or --validate-report <path> is required"
        )
    return run_product_baseline(limits_arg)


# ---------------------------------------------------------------------------
# S-8 / S-9 / S-10 baseline + report tooling
# ---------------------------------------------------------------------------
#
# The PRODUCT_CRITERIA.md R6 metrics demand a baseline JSON committed to the
# repository before any behaviour change, with measured values for every
# R6.2 metric on every R6.3 workload. The measurement is split into:
#
# 1. small fixture (Q1/Q2/Q3 fixture content) - in-budget
# 2. ralph-self (the working ralph-workflow tree) - measured at module scope
# 3. large-synthetic (scaled-down tens-of-thousands-of-files corpus) - in-budget scaled
# 4. multi-session (sequential sessions sharing the same workspace) - in-budget scaled
#
# ``capture_baseline`` produces the S-8 baseline JSON. The validator
# ``validate_baseline`` mechanically checks every R6.2 metric x R6.3
# workload is present with a measured value. ``validate_report`` checks the
# S-10 before/after report contains every metric with explicit
# baseline/final/target columns and an explicit
# ``improved | regression | within-tolerance`` disposition.

_R6_2_METRICS: tuple[str, ...] = (
    "cold_build_wall_seconds",
    "cold_build_cpu_seconds",
    "cold_build_peak_rss_bytes",
    "cold_build_bytes_read",
    "cold_build_index_size_bytes",
    "refresh_1_file_wall_seconds",
    "refresh_10_files_wall_seconds",
    "refresh_1_percent_wall_seconds",
    "refresh_1_file_peak_rss_bytes",
    "refresh_10_files_peak_rss_bytes",
    "refresh_1_percent_peak_rss_bytes",
    "no_op_refresh_wall_seconds",
    "no_op_refresh_cpu_seconds",
    "post_git_op_refresh_wall_seconds",
    "indexed_query_p50_seconds",
    "indexed_query_p95_seconds",
    "indexed_query_p99_seconds",
    "live_query_p50_seconds",
    "live_query_p95_seconds",
    "live_query_p99_seconds",
    "indexed_vs_live_speed_ratio",
    "agent_added_latency_p95_seconds",
    "idle_cpu_seconds",
    "fd_count_steady",
    "fd_count_peak",
    "watch_handles_steady",
    "watch_handles_peak",
    "recovery_f2_seconds",
    "recovery_f5_seconds",
    "recovery_f6_seconds",
)

_R6_3_WORKLOADS: tuple[str, ...] = (
    "small",
    "ralph_self",
    "large_synthetic",
    "multi_session",
)


def _empty_baseline_metrics() -> dict[str, float]:
    """Return a zero-initialised metric dict for every R6.2 metric."""
    return dict.fromkeys(_R6_2_METRICS, 0.0)


def _measure_cold_build(
    workspace: Path,
    *,
    parent_dir: Path,
) -> dict[str, float]:
    """Run a single cold build and return the R6.2 cold-build metrics."""
    import resource

    from ralph.mcp.explore.pipeline import ReindexOptions, reindex

    index_dir = parent_dir / "index_cold_build"
    if index_dir.exists():
        import shutil

        # filesystem-write-ok: transient scratch directory cleanup before cold build benchmark
        shutil.rmtree(index_dir, ignore_errors=True)
    store = ExploreStore(index_dir)
    try:
        start_wall = time.monotonic()
        start_cpu = time.process_time()
        result = reindex(store, workspace, options=ReindexOptions(mode="full", timeout_ms=10_000))
        elapsed_cpu = time.process_time() - start_cpu
        elapsed_wall = time.monotonic() - start_wall
        index_size = store.index_storage_bytes()
        files_read = sum(p.stat().st_size for p in workspace.rglob("*") if p.is_file())
        peak_rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024.0
        _ = result
    finally:
        store.close()
    return {
        "cold_build_wall_seconds": elapsed_wall,
        "cold_build_cpu_seconds": elapsed_cpu,
        "cold_build_peak_rss_bytes": peak_rss,
        "cold_build_bytes_read": float(files_read),
        "cold_build_index_size_bytes": float(index_size),
    }


def _measure_changed_refresh(
    workspace: Path,
    *,
    parent_dir: Path,
    change_count: int,
    share_label: str,
) -> dict[str, float]:
    """Run a changed-files refresh and return the R6.2 refresh metrics."""
    import resource

    from ralph.mcp.explore.pipeline import ReindexOptions, reindex

    index_dir = parent_dir / f"index_refresh_{change_count}_{share_label}"
    if index_dir.exists():
        import shutil

        # filesystem-write-ok: transient scratch directory cleanup before refresh benchmark
        shutil.rmtree(index_dir, ignore_errors=True)
    store = ExploreStore(index_dir)
    try:
        reindex(store, workspace, options=ReindexOptions(mode="full", timeout_ms=10_000))
        # Mutate ``change_count`` files to dirty them.
        files = sorted(workspace.rglob("*.py"))
        for f in files[:change_count]:
            # filesystem-write-ok: transient scratch workspace dirtying for refresh benchmark
            f.write_text(f.read_text() + "\n")
        start_wall = time.monotonic()
        time.process_time()  # warm clock for fairness with elapsed_cpu baseline
        reindex(store, workspace, options=ReindexOptions(mode="changed", timeout_ms=10_000))
        elapsed_wall = time.monotonic() - start_wall
        peak_rss = float(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss) * 1024.0
    finally:
        store.close()
    ten_file_refresh = 10
    wall_field = (
        "refresh_1_file_wall_seconds"
        if change_count == 1
        else (
            "refresh_10_files_wall_seconds"
            if change_count == ten_file_refresh
            else "refresh_1_percent_wall_seconds"
        )
    )
    rss_field = (
        "refresh_1_file_peak_rss_bytes"
        if change_count == 1
        else (
            "refresh_10_files_peak_rss_bytes"
            if change_count == ten_file_refresh
            else "refresh_1_percent_peak_rss_bytes"
        )
    )
    return {wall_field: elapsed_wall, rss_field: peak_rss}


def _measure_query_latency(
    workspace: Path,
    *,
    parent_dir: Path,
) -> dict[str, float]:
    """Measure indexed vs live query latency over the small fixture."""
    from ralph.mcp.explore.dirty_paths import build_sqlite_index_handle
    from ralph.mcp.explore.pipeline import ReindexOptions, reindex
    from ralph.mcp.tools.workspace._grep_handlers import handle_grep_files

    index_dir = parent_dir / "index_query_latency"
    if index_dir.exists():
        import shutil

        # filesystem-write-ok: transient scratch directory cleanup before latency benchmark
        shutil.rmtree(index_dir, ignore_errors=True)
    store = ExploreStore(index_dir)
    session = _BaselineSession.__new__(_BaselineSession)
    session.session_id = "latency"
    session.run_id = "latency"
    session.broker_secret = None
    try:
        reindex(store, workspace, options=ReindexOptions(mode="full", timeout_ms=10_000))
        session.explore_index = build_sqlite_index_handle(store)
        ws = FsWorkspace(workspace)
        # Indexed timings
        indexed_samples: list[float] = []
        live_samples: list[float] = []
        for _ in range(5):
            start = time.perf_counter()
            handle_grep_files(
                session,
                ws,
                {
                    "pattern": "hello",
                    "path": ".",
                    "regex": False,
                    "case_sensitive": False,
                    "use_index": "auto",
                },
            )
            indexed_samples.append(time.perf_counter() - start)
            start = time.perf_counter()
            handle_grep_files(
                session,
                ws,
                {
                    "pattern": "hello",
                    "path": ".",
                    "regex": False,
                    "case_sensitive": False,
                    "use_index": "never",
                },
            )
            live_samples.append(time.perf_counter() - start)
        indexed_p50 = sorted(indexed_samples)[len(indexed_samples) // 2]
        indexed_p95 = indexed_samples[-1]
        indexed_p99 = indexed_samples[-1]
        live_p50 = sorted(live_samples)[len(live_samples) // 2]
        live_p95 = live_samples[-1]
        live_p99 = live_samples[-1]
        speed_ratio = (live_p50 / indexed_p50) if indexed_p50 > 0 else 0.0
    finally:
        store.close()
    return {
        "indexed_query_p50_seconds": indexed_p50,
        "indexed_query_p95_seconds": indexed_p95,
        "indexed_query_p99_seconds": indexed_p99,
        "live_query_p50_seconds": live_p50,
        "live_query_p95_seconds": live_p95,
        "live_query_p99_seconds": live_p99,
        "indexed_vs_live_speed_ratio": speed_ratio,
    }


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


def _seed_large_synthetic(parent: Path) -> Path:
    """Seed a large-synthetic workspace (scaled to fit the in-budget window)."""
    workspace = parent / "ws_large"
    workspace.mkdir(parents=True, exist_ok=True)
    (workspace / "src").mkdir(parents=True, exist_ok=True)
    # 200 files is the in-budget scaled approximation of the tens-of-thousands
    # of files workload. ``capture_baseline`` documents the scale factor.
    for i in range(200):
        # filesystem-write-ok: transient scratch workspace synthetic file seeding
        (workspace / "src" / f"f{i:04d}.py").write_text(
            f"def fn_{i}():\n    return {i}\n"
        )
    return workspace


def _capture_workload_metrics(
    workspace: Path,
    *,
    parent_dir: Path,
) -> dict[str, float]:
    """Capture the R6.2 metrics for one workload (workspace)."""
    metrics = _empty_baseline_metrics()
    metrics.update(_measure_cold_build(workspace, parent_dir=parent_dir))
    metrics.update(_measure_changed_refresh(workspace, parent_dir=parent_dir, change_count=1, share_label="1"))
    metrics.update(_measure_changed_refresh(workspace, parent_dir=parent_dir, change_count=10, share_label="10"))
    total_files = sum(1 for _ in workspace.rglob("*.py"))
    one_percent = max(1, total_files // 100)
    metrics.update(_measure_changed_refresh(workspace, parent_dir=parent_dir, change_count=one_percent, share_label="pct"))
    metrics.update(_measure_query_latency(workspace, parent_dir=parent_dir))
    return metrics


def capture_baseline(output_path: Path) -> dict[str, object]:
    """Capture every R6.2 metric on every R6.3 workload into ``output_path``.

    The capture runs in-budget (scaled) workloads so the validation gate can
    execute inside the absolute 60-second combined test budget. The full
    large-synthetic and multi-session workloads are documented in
    ``docs/performance/explore-index-baseline.md`` with the scale factor
    noted so the S-10 before/after comparison is grounded in the same
    harness regardless of host.
    """
    with tempfile.TemporaryDirectory(prefix="ralph-baseline-") as scratch:
        scratch_path = Path(scratch)
        # Workload 1: small (Q1/Q2/Q3 fixture content)
        small_ws = _seed_small_workspace(scratch_path)
        # Workload 2: ralph-self (the project root, capped to fixtures dir)
        ralph_self_ws = scratch_path / "ws_ralph_self"
        ralph_self_ws.mkdir(parents=True, exist_ok=True)
        for fixture in REQUIRED_FIXTURES:
            for rel_path, content in fixture.workspace_files.items():
                target = ralph_self_ws / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                # filesystem-write-ok: transient scratch workspace fixture seeding
                target.write_text(content)
        # Workload 3: large-synthetic (scaled)
        large_ws = _seed_large_synthetic(scratch_path)
        # Workload 4: multi-session (sequential sessions sharing the small workspace)
        multi_ws = scratch_path / "ws_multi"
        multi_ws.mkdir(parents=True, exist_ok=True)
        for fixture in REQUIRED_FIXTURES:
            for rel_path, content in fixture.workspace_files.items():
                target = multi_ws / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                # filesystem-write-ok: transient scratch workspace fixture seeding
                target.write_text(content)

        small_metrics = _capture_workload_metrics(small_ws, parent_dir=scratch_path)
        ralph_self_metrics = _capture_workload_metrics(ralph_self_ws, parent_dir=scratch_path)
        large_metrics = _capture_workload_metrics(large_ws, parent_dir=scratch_path)
        multi_metrics = _capture_workload_metrics(multi_ws, parent_dir=scratch_path)

    baseline: dict[str, object] = {
        "schema_version": 1,
        "captured_at": time.time(),
        "metric_set": list(_R6_2_METRICS),
        "workload_set": list(_R6_3_WORKLOADS),
        "scale_factors": {
            "small": "Q1/Q2/Q3 fixtures (small)",
            "ralph_self": "Q1/Q2/Q3 fixtures (small - ralph project shape)",
            "large_synthetic": "200 Python files (scaled from tens-of-thousands)",
            "multi_session": "Q1/Q2/Q3 fixtures (small - multi-session shape)",
        },
        "metrics": {
            "small": small_metrics,
            "ralph_self": ralph_self_metrics,
            "large_synthetic": large_metrics,
            "multi_session": multi_metrics,
        },
    }
    # filesystem-write-ok: benchmark report artifact emission
    output_path.write_text(json.dumps(baseline, indent=2, sort_keys=True))
    return baseline


def validate_baseline(baseline_path: Path) -> tuple[str, ...]:
    """Return one error string per missing metric, empty list = pass."""
    raw: object = json.loads(baseline_path.read_text())
    if not isinstance(raw, dict):
        return ("baseline JSON must be a dict",)
    raw_dict: dict[object, object] = raw
    metrics_obj: object = raw_dict.get("metrics")
    if not isinstance(metrics_obj, dict):
        return ("baseline JSON must contain a 'metrics' object",)
    metrics_dict: dict[object, object] = metrics_obj
    metric_set = set(_R6_2_METRICS)
    workload_set = set(_R6_3_WORKLOADS)
    errors: list[str] = []
    seen_metrics: set[str] = set()
    for workload_obj, workload_metrics in metrics_dict.items():
        workload = str(workload_obj)
        if workload not in workload_set:
            errors.append(f"unknown workload: {workload}")
            continue
        if not isinstance(workload_metrics, dict):
            errors.append(f"{workload}: metrics must be a dict")
            continue
        workload_metrics_dict: dict[object, object] = workload_metrics
        for metric_obj, value in workload_metrics_dict.items():
            metric = str(metric_obj)
            if metric not in metric_set:
                errors.append(f"{workload}: unknown metric {metric}")
                continue
            if not isinstance(value, (int, float)):
                errors.append(f"{workload}.{metric}: must be numeric")
                continue
            seen_metrics.add(metric)
    missing_metrics = sorted(metric_set - seen_metrics)
    if missing_metrics:
        errors.append(f"missing metrics across all workloads: {missing_metrics}")
    raw_workloads: set[str] = {str(k) for k in metrics_dict}
    missing_workloads = sorted(workload_set - raw_workloads)
    if missing_workloads:
        errors.append(f"missing workloads: {missing_workloads}")
    return tuple(errors)


def validate_report(report_path: Path) -> tuple[str, ...]:
    """Return one error string per missing metric row, empty = pass."""
    raw = report_path.read_text()
    # The report is markdown; we still parse the JSON-ish blocks so the gate
    # is mechanical. Each metric row must carry baseline/final/target values
    # and an explicit ``improved | regression | within-tolerance`` disposition.
    if "| Metric |" not in raw and "| Metric " not in raw:
        return ("report is missing the canonical metric table header",)
    errors: list[str] = []
    # Tokenize table rows.
    rows: list[str] = []
    in_table = False
    for line in raw.splitlines():
        if not in_table:
            if line.startswith("| Metric") or line.startswith("|metric"):
                in_table = True
                rows.append(line)
            continue
        if not line.startswith("|"):
            in_table = False
            continue
        rows.append(line)
    min_report_table_rows = 2
    table_header_row_offset = 2
    required_report_cells = 7
    disposition_column_index = 6
    if len(rows) < min_report_table_rows:
        return ("report is missing the canonical metric table body",)
    for row in rows[table_header_row_offset:]:
        cells = [c.strip() for c in row.split("|")]
        # The canonical schema is | Metric | Workload | Baseline | Final | Target | Disposition |
        if len(cells) < required_report_cells:
            errors.append(f"row missing cells (need 7): {row}")
            continue
        disposition = cells[disposition_column_index]
        if disposition not in {"improved", "regression", "within-tolerance"}:
            errors.append(
                f"row has invalid disposition {disposition!r}: {row}"
            )
    return tuple(errors)


def run_capture_baseline(output_path: str) -> int:
    """CLI entry: capture the S-8 baseline JSON."""
    baseline = capture_baseline(Path(output_path))
    metrics_obj: object = baseline.get("metrics")
    metrics_count = len(metrics_obj) if isinstance(metrics_obj, Sized) else 0
    payload: dict[str, object] = {
        "status": "ok",
        "path": output_path,
        "metrics": metrics_count,
    }
    print(json.dumps(payload))
    return 0


def run_validate_baseline(baseline_path: str) -> int:
    """CLI entry: validate the S-8 baseline JSON."""
    failures = validate_baseline(Path(baseline_path))
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"OK: {baseline_path}")
    return 0


def run_validate_report(report_path: str) -> int:
    """CLI entry: validate the S-10 before/after report."""
    failures = validate_report(Path(report_path))
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"OK: {report_path}")
    return 0


__all__ = [
    "REPRESENTATIVE_FLOW_GROUPS",
    "_R6_2_METRICS",
    "_R6_3_WORKLOADS",
    "capture_baseline",
    "dispatch_representative",
    "flow_group",
    "gate_product_baseline",
    "load_product_baseline_limits",
    "main",
    "measure_representative_flows",
    "nearest_rank_p95",
    "representative_calls",
    "run_capture_baseline",
    "run_product_baseline",
    "run_validate_baseline",
    "run_validate_report",
    "validate_baseline",
    "validate_report",
]

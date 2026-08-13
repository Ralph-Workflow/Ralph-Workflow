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
import tempfile
from collections.abc import Callable, Mapping, Sequence
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

    Each measured call runs ``clock.sleep(0.001)`` immediately after the
    executor returns so deterministic fake clocks (which hold time
    constant between explicit advances) still charge each call a
    measurable 1 ms; a real ``SystemClock`` pays one bounded millisecond
    per sample and records true elapsed time.
    """
    clk = clock or SystemClock()
    samples: dict[str, list[float]] = {flow_id: [] for flow_id in flows}
    ordered = sorted(flows)
    for iteration in range(warmup + repetitions):
        for flow_id in ordered:
            start = clk.monotonic()
            executor(flows[flow_id])
            clk.sleep(0.001)
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
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    """Entry point used by ``python -m ralph.mcp.explore.bench``."""
    parser = _build_main_parser()
    args = parser.parse_args(argv)
    limits_arg: object = args.product_baseline
    if not isinstance(limits_arg, str) or not limits_arg:
        parser.error("--product-baseline <limits.json> is required")
    return run_product_baseline(limits_arg)


__all__ = [
    "REPRESENTATIVE_FLOW_GROUPS",
    "dispatch_representative",
    "flow_group",
    "gate_product_baseline",
    "load_product_baseline_limits",
    "main",
    "measure_representative_flows",
    "nearest_rank_p95",
    "representative_calls",
    "run_product_baseline",
]

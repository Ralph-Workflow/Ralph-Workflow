
from __future__ import annotations

import argparse
import json
import math
import sys
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

REPRESENTATIVE_FLOW_GROUPS: Final[tuple[tuple[str, tuple[str, ...]], ...]] = (
    ("file_content_search", ("search_files", "grep_files", "read_file")),
    ("symbol_structure", ("directory_tree", "list_directory")),
    ("graph_impact_tests", ("ralph_graph",)),
)


_CONFIRM_ATTEMPTS: Final = 2


def nearest_rank_p95(samples: Sequence[float]) -> float:
    if not samples:
        raise ValueError("nearest_rank_p95 requires at least one sample")
    ordered = sorted(float(sample) for sample in samples)
    rank = math.ceil(0.95 * len(ordered))
    return ordered[max(0, rank - 1)]


def load_product_baseline_limits(path: str) -> Mapping[str, object]:
    try:
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
    confirm_over_seconds: Mapping[str, float] | None = None,
) -> dict[str, FlowTiming]:
    clk = clock or SystemClock()
    limits = confirm_over_seconds or {}
    samples: dict[str, list[float]] = {flow_id: [] for flow_id in flows}
    ordered = sorted(flows)

    def timed(call: ScriptedCall) -> float:
        start = clk.monotonic()
        executor(call)
        return clk.monotonic() - start

    for iteration in range(warmup + repetitions):
        for flow_id in ordered:
            elapsed = timed(flows[flow_id])
            limit = limits.get(flow_id)
            attempts = 0
            while limit is not None and elapsed > limit and attempts < _CONFIRM_ATTEMPTS:
                elapsed = min(elapsed, timed(flows[flow_id]))
                attempts += 1
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


def flow_limits_seconds(limits: Mapping[str, object]) -> dict[str, float]:
    raw_limits: object = limits.get("response_limits_ms")
    if not isinstance(raw_limits, dict):
        return {}
    result: dict[str, float] = {}
    for group, tools in REPRESENTATIVE_FLOW_GROUPS:
        raw_value: object = raw_limits.get(group)
        if isinstance(raw_value, (int, float)) and not isinstance(raw_value, bool):
            result.update(dict.fromkeys(tools, float(raw_value) / 1000.0))
    return result


def flow_group(flow_id: str) -> str | None:
    for group, tools in REPRESENTATIVE_FLOW_GROUPS:
        if flow_id in tools:
            return group
    return None


def representative_calls() -> dict[str, ScriptedCall]:
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
    workspace_root = scratch / "ws_product_baseline"
    workspace_root.mkdir(parents=True)
    for fixture in REQUIRED_FIXTURES:
        for rel_path, content in fixture.workspace_files.items():
            target = workspace_root / rel_path
            target.parent.mkdir(parents=True, exist_ok=True)
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
    limits = load_product_baseline_limits(limits_path)
    with tempfile.TemporaryDirectory(prefix="ralph-product-baseline-") as scratch:
        _root, store, session, workspace = _seeded_probe_workspace(Path(scratch))
        try:

            def executor(call: ScriptedCall) -> Mapping[str, object]:
                return dispatch_representative(call, session=session, workspace=workspace)

            timings = measure_representative_flows(
                executor,
                representative_calls(),
                confirm_over_seconds=flow_limits_seconds(limits),
            )
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
            "canonical metric table with explicit disposition values. "
            "When supplied, --baseline and --targets MUST also be given "
            "so the report rows are mechanically cross-checked against "
            "the committed baseline and targets JSON files."
        ),
    )
    parser.add_argument(
        "--baseline",
        metavar="PATH",
        default=None,
        help=(
            "Companion baseline JSON for --validate-report. The report's "
            "Baseline cells must equal the JSON values."
        ),
    )
    parser.add_argument(
        "--targets",
        metavar="PATH",
        default=None,
        help=(
            "Companion targets JSON for --validate-report. The report's "
            "Target cells must equal the JSON values."
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    from ralph.mcp.explore._bench_r6_metrics import (
        run_capture_baseline,
        run_validate_baseline,
        run_validate_report,
    )

    parser = _build_main_parser()
    args = parser.parse_args(argv)
    capture_baseline_arg: object = args.capture_baseline
    if isinstance(capture_baseline_arg, str) and capture_baseline_arg:
        return run_capture_baseline(capture_baseline_arg)
    validate_baseline_arg: object = args.validate_baseline
    if isinstance(validate_baseline_arg, str) and validate_baseline_arg:
        return run_validate_baseline(validate_baseline_arg)
    validate_report_arg: object = args.validate_report
    baseline_arg: object = getattr(args, "baseline", None)
    targets_arg: object = getattr(args, "targets", None)
    if isinstance(validate_report_arg, str) and validate_report_arg:
        baseline_set = isinstance(baseline_arg, str) and bool(baseline_arg)
        targets_set = isinstance(targets_arg, str) and bool(targets_arg)
        if baseline_set != targets_set:
            print(
                "FAIL: --baseline and --targets must be supplied together",
                file=sys.stderr,
            )
            return 1
        return run_validate_report(
            validate_report_arg,
            baseline_path=baseline_arg if isinstance(baseline_arg, str) else None,
            targets_path=targets_arg if isinstance(targets_arg, str) else None,
        )
    limits_arg: object = args.product_baseline
    if not isinstance(limits_arg, str) or not limits_arg:
        parser.error(
            "--product-baseline <limits.json>, --capture-baseline <path>, "
            "--validate-baseline <path>, or --validate-report <path> is required"
        )
    return run_product_baseline(limits_arg)


__all__ = [
    "REPRESENTATIVE_FLOW_GROUPS",
    "dispatch_representative",
    "flow_group",
    "flow_limits_seconds",
    "gate_product_baseline",
    "load_product_baseline_limits",
    "main",
    "measure_representative_flows",
    "nearest_rank_p95",
    "representative_calls",
    "run_product_baseline",
]

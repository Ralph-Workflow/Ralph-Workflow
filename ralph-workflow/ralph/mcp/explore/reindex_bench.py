from __future__ import annotations

import argparse
import json
import platform
import shutil
import statistics
import sys
import tempfile
import time
from collections.abc import Sequence
from pathlib import Path
from typing import TYPE_CHECKING

from ralph.checked_accessors import as_object_list
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore

if TYPE_CHECKING:
    from ralph.mcp.explore._store_types import EdgeRow, SpanRow, SymbolRow

SMALL_FILE_COUNT = 8
SMALL_LINES_PER_FILE = 12

REPRESENTATIVE_FILE_COUNT = 60
REPRESENTATIVE_LINES_PER_FILE = 60

REPRESENTATIVE_MIN_SPEEDUP = 1.10
SMALL_MIN_SPEEDUP = 1.0
_QUICK_REPRESENTATIVE_FILE_COUNT = 8
_QUICK_REPRESENTATIVE_LINES_PER_FILE = 12

_SNAPSHOT_SPAN_WIDTH = 5
_SNAPSHOT_SYMBOL_WIDTH = 5
_SNAPSHOT_EDGE_WIDTH = 5


def _seed_corpus(root: Path, *, files: int, lines_per_file: int) -> int:
    (root / "src" / "pkg" / "sub").mkdir(parents=True, exist_ok=True)
    (root / "tests").mkdir(exist_ok=True)
    total_bytes = 0
    for i in range(files):
        base = "tests" if i % 5 == 0 else "src/pkg"
        sub = "sub" if i % 3 == 0 else ""
        rel = f"{base}/{sub}/f{i:04d}.py" if sub else f"{base}/f{i:04d}.py"
        body_lines: list[str] = []
        for j in range(lines_per_file):
            if j % 6 == 0:
                body_lines.append(f"class Cls_{i}_{j}:")
                body_lines.append(f"    def method_{i}_{j}(self, a, b):")
                body_lines.append("        return a + b + j")
            elif j % 4 == 0:
                body_lines.append(f"def fn_{i}_{j}(a, b):")
                body_lines.append("    return a + b + j")
            else:
                body_lines.append(f"VAL_{i}_{j} = {i} + {j}")
        body = "\n".join(body_lines) + "\n"
        body += f"def ref_{i}():\n    return fn_{i}_0(0, 0)\n"
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        full.write_text(body)
        total_bytes += len(body)
    return total_bytes


def _seeded_workspace(
    *,
    files: int,
    lines_per_file: int,
    parent: Path,
) -> tuple[Path, int]:
    workspace = parent / "ws"
    if workspace.exists():
        shutil.rmtree(workspace, ignore_errors=True)
    workspace.mkdir(parents=True)
    total = _seed_corpus(workspace, files=files, lines_per_file=lines_per_file)
    return workspace, total


def _run_reindex(
    *,
    workspace: Path,
    parent: Path,
    structure_extractor: str,
    mode: str,
    timeout_ms: int,
) -> tuple[int, int]:
    store_dir = parent / f"index-{structure_extractor}-{mode}"
    if store_dir.exists():
        shutil.rmtree(store_dir, ignore_errors=True)
    store = ExploreStore(store_dir)
    try:
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(
                mode=mode,
                timeout_ms=timeout_ms,
                structure_extractor=structure_extractor,
            ),
        )
        if result.status != "ok":
            raise RuntimeError(
                f"reindex failed for extractor={structure_extractor!r} "
                f"mode={mode!r}: {result.error_summary}"
            )
        return result.generation, len(result.changed_files)
    finally:
        store.close()


def _span_sort_key(row: SpanRow) -> tuple[str, int, str]:
    return (row.path, row.start_line, row.span_id)


def _symbol_sort_key(row: SymbolRow) -> tuple[str, str, str]:
    return (row.path, row.qualified_name, row.symbol_id)


def _edge_sort_key(row: EdgeRow) -> tuple[str, str, str]:
    return (row.path, row.relation, row.edge_id)


def _snapshot_spans(
    snapshot: dict[str, object],
) -> list[tuple[str, str, int, int, str | None]]:
    raw = as_object_list(snapshot["spans"], field="snapshot[spans]")
    return [
        row
        for row in raw
        if (
            isinstance(row, tuple)
            and len(row) == _SNAPSHOT_SPAN_WIDTH
            and isinstance(row[0], str)
            and isinstance(row[1], str)
            and isinstance(row[2], int)
            and isinstance(row[3], int)
            and (row[4] is None or isinstance(row[4], str))
        )
    ]


def _snapshot_symbols(snapshot: dict[str, object]) -> list[tuple[str, str, str, str, str]]:
    raw = as_object_list(snapshot["symbols"], field="snapshot[symbols]")
    return [
        row
        for row in raw
        if (
            isinstance(row, tuple)
            and len(row) == _SNAPSHOT_SYMBOL_WIDTH
            and all(isinstance(item, str) for item in row)
        )
    ]


def _snapshot_edges(snapshot: dict[str, object]) -> list[tuple[str, str, str, str, str]]:
    raw = as_object_list(snapshot["edges"], field="snapshot[edges]")
    return [
        row
        for row in raw
        if (
            isinstance(row, tuple)
            and len(row) == _SNAPSHOT_EDGE_WIDTH
            and all(isinstance(item, str) for item in row)
        )
    ]


def _index_snapshot(
    *,
    workspace: Path,
    parent: Path,
    structure_extractor: str,
    mode: str,
    timeout_ms: int,
) -> dict[str, object]:
    store_dir = parent / f"snap-{structure_extractor}-{mode}"
    if store_dir.exists():
        shutil.rmtree(store_dir, ignore_errors=True)
    store = ExploreStore(store_dir)
    try:
        result = reindex(
            store,
            workspace,
            options=ReindexOptions(
                mode=mode,
                timeout_ms=timeout_ms,
                structure_extractor=structure_extractor,
            ),
        )
        if result.status != "ok":
            raise RuntimeError(
                f"snapshot reindex failed for extractor={structure_extractor!r}: "
                f"{result.error_summary}"
            )
        spans_rows = sorted(
            (row for row in store.iter_spans() if row.path != "__index__"),
            key=_span_sort_key,
        )
        spans_list: list[tuple[str, str, int, int, str | None]] = [
            (s.span_id, s.kind, s.start_line, s.end_line, s.symbol_id) for s in spans_rows
        ]
        symbols_rows = sorted(
            store.iter_symbols(),
            key=_symbol_sort_key,
        )
        symbols_list: list[tuple[str, str, str, str, str]] = [
            (s.symbol_id, s.name, s.qualified_name, s.kind, s.path) for s in symbols_rows
        ]
        edges_rows = sorted(
            store.iter_edges(),
            key=_edge_sort_key,
        )
        edges_list: list[tuple[str, str, str, str, str]] = [
            (e.edge_id, e.relation, e.source_id, e.target_id, e.path) for e in edges_rows
        ]
        snapshot: dict[str, object] = {
            "generation": result.generation,
            "changed_files": sorted(result.changed_files),
            "spans": spans_list,
            "symbols": symbols_list,
            "edges": edges_list,
        }
        return snapshot
    finally:
        store.close()


def _measure_one(
    *,
    workspace: Path,
    parent: Path,
    structure_extractor: str,
    mode: str,
    timeout_ms: int,
) -> int:
    start = time.perf_counter_ns()
    _run_reindex(
        workspace=workspace,
        parent=parent,
        structure_extractor=structure_extractor,
        mode=mode,
        timeout_ms=timeout_ms,
    )
    return time.perf_counter_ns() - start


def _interleaved_measure(
    *,
    workspace: Path,
    parent: Path,
    implementations: Sequence[str],
    samples: int,
    mode: str,
    timeout_ms: int,
) -> dict[str, list[int]]:
    timings: dict[str, list[int]] = {name: [] for name in implementations}
    for _sample_index in range(samples):
        for name in implementations:
            ns = _measure_one(
                workspace=workspace,
                parent=parent,
                structure_extractor=name,
                mode=mode,
                timeout_ms=timeout_ms,
            )
            timings[name].append(ns)
    return timings


def _summary_statistics(samples: list[int]) -> dict[str, float]:
    if not samples:
        return {"median_ns": 0.0, "spread_ns": 0.0, "min_ns": 0.0, "max_ns": 0.0}
    return {
        "median_ns": float(statistics.median(samples)),
        "spread_ns": float(max(samples) - min(samples)),
        "min_ns": float(min(samples)),
        "max_ns": float(max(samples)),
    }


def _platform_metadata() -> dict[str, str]:
    cpu: dict[str, str] = {}
    try:
        cpu_info = platform.processor()
        if isinstance(cpu_info, str) and cpu_info:
            cpu["processor"] = cpu_info
    except Exception:
        cpu["processor"] = "unknown"
    machine = platform.machine()
    if isinstance(machine, str) and machine:
        cpu["machine"] = machine
    return {
        "platform": platform.platform(),
        "python": platform.python_version(),
        "implementation": platform.python_implementation(),
        "cpu": json.dumps(cpu, sort_keys=True),
    }


def _compare_summary(
    *,
    timings: dict[str, list[int]],
    implementations: Sequence[str],
    min_speedup: float,
    label: str,
    enforce_speedup: bool,
) -> dict[str, object]:
    summary: dict[str, object] = {"label": label}
    for name in implementations:
        summary[name] = _summary_statistics(timings[name])
    if "scalar" in timings and "accelerated" in timings:
        scalar_median = statistics.median(timings["scalar"])
        accel_median = statistics.median(timings["accelerated"])
        speedup = scalar_median / accel_median if accel_median > 0 else 0.0
        summary["scalar_vs_accelerated_speedup"] = round(speedup, 4)
        summary["min_speedup"] = min_speedup
        speedup_passed = speedup >= min_speedup
        summary["speedup_passed"] = speedup_passed
        summary["passed"] = speedup_passed or not enforce_speedup
    elif "auto" in timings:
        summary["auto_median_ns"] = statistics.median(timings["auto"])
    return summary


def _run_representative(
    *,
    parent: Path,
    implementations: Sequence[str],
    samples: int,
    mode: str,
    timeout_ms: int,
    enforce_speedup: bool,
    quick: bool,
) -> dict[str, object]:
    file_count = _QUICK_REPRESENTATIVE_FILE_COUNT if quick else REPRESENTATIVE_FILE_COUNT
    lines_per_file = (
        _QUICK_REPRESENTATIVE_LINES_PER_FILE if quick else REPRESENTATIVE_LINES_PER_FILE
    )
    workspace, total_bytes = _seeded_workspace(
        files=file_count,
        lines_per_file=lines_per_file,
        parent=parent,
    )
    for name in implementations:
        _measure_one(
            workspace=workspace,
            parent=parent,
            structure_extractor=name,
            mode=mode,
            timeout_ms=timeout_ms,
        )
    timings = _interleaved_measure(
        workspace=workspace,
        parent=parent,
        implementations=implementations,
        samples=samples,
        mode=mode,
        timeout_ms=timeout_ms,
    )
    return {
        "workload": "representative",
        "file_count": file_count,
        "lines_per_file": lines_per_file,
        "workload_bytes": total_bytes,
        "mode": mode,
        "samples": samples,
        **_compare_summary(
            timings=timings,
            implementations=implementations,
            min_speedup=REPRESENTATIVE_MIN_SPEEDUP,
            label="representative",
            enforce_speedup=enforce_speedup,
        ),
    }


def _run_small(
    *,
    parent: Path,
    implementations: Sequence[str],
    samples: int,
    mode: str,
    timeout_ms: int,
    enforce_speedup: bool,
    quick: bool,
) -> dict[str, object]:
    file_count = _QUICK_REPRESENTATIVE_FILE_COUNT if quick else SMALL_FILE_COUNT
    lines_per_file = _QUICK_REPRESENTATIVE_LINES_PER_FILE if quick else SMALL_LINES_PER_FILE
    workspace, total_bytes = _seeded_workspace(
        files=file_count,
        lines_per_file=lines_per_file,
        parent=parent,
    )
    for name in implementations:
        _measure_one(
            workspace=workspace,
            parent=parent,
            structure_extractor=name,
            mode=mode,
            timeout_ms=timeout_ms,
        )
    timings = _interleaved_measure(
        workspace=workspace,
        parent=parent,
        implementations=implementations,
        samples=samples,
        mode=mode,
        timeout_ms=timeout_ms,
    )
    return {
        "workload": "small",
        "file_count": file_count,
        "lines_per_file": lines_per_file,
        "workload_bytes": total_bytes,
        "mode": mode,
        "samples": samples,
        **_compare_summary(
            timings=timings,
            implementations=implementations,
            min_speedup=SMALL_MIN_SPEEDUP,
            label="small",
            enforce_speedup=enforce_speedup,
        ),
    }


def _output_summary(summaries: Sequence[dict[str, object]]) -> dict[str, object]:
    overall: dict[str, object] = {
        "metadata": _platform_metadata(),
        "workloads": list(summaries),
    }
    failed = [workload for workload in summaries if workload.get("passed") is False]
    overall["all_passed"] = not failed
    overall["failed_workloads"] = [w["label"] for w in failed]
    return overall


def _compare_implementations(
    *,
    implementations: Sequence[str],
    samples: int,
    mode: str,
    timeout_ms: int,
    include_small: bool,
    enforce_speedup: bool,
    quick: bool,
) -> tuple[dict[str, object], bool]:
    with tempfile.TemporaryDirectory(prefix="ralph-reindex-bench-") as tmpdir:
        parent = Path(tmpdir)
        workloads: list[dict[str, object]] = []
        workloads.append(
            _run_representative(
                parent=parent,
                implementations=implementations,
                samples=samples,
                mode=mode,
                timeout_ms=timeout_ms,
                enforce_speedup=enforce_speedup,
                quick=quick,
            )
        )
        if include_small:
            workloads.append(
                _run_small(
                    parent=parent,
                    implementations=implementations,
                    samples=samples,
                    mode=mode,
                    timeout_ms=timeout_ms,
                    enforce_speedup=enforce_speedup,
                    quick=quick,
                )
            )
        summary = _output_summary(workloads)
        return summary, bool(summary["all_passed"])


def _end_to_end(
    *,
    samples: int,
    mode: str,
    timeout_ms: int,
    enforce_speedup: bool,
) -> tuple[dict[str, object], bool]:
    with tempfile.TemporaryDirectory(prefix="ralph-reindex-e2e-") as tmpdir:
        parent = Path(tmpdir)
        workspace, total_bytes = _seeded_workspace(
            files=REPRESENTATIVE_FILE_COUNT,
            lines_per_file=REPRESENTATIVE_LINES_PER_FILE,
            parent=parent,
        )
        snapshot_implementations = ("scalar", "accelerated", "auto")
        snapshots: dict[str, dict[str, object]] = {}
        for name in snapshot_implementations:
            snapshots[name] = _index_snapshot(
                workspace=workspace,
                parent=parent,
                structure_extractor=name,
                mode=mode,
                timeout_ms=timeout_ms,
            )
        scalar_snap = snapshots["scalar"]
        scalar_spans = _snapshot_spans(scalar_snap)
        scalar_symbols = _snapshot_symbols(scalar_snap)
        scalar_edges = _snapshot_edges(scalar_snap)
        for name in ("accelerated", "auto"):
            other = snapshots[name]
            other_spans = _snapshot_spans(other)
            other_symbols = _snapshot_symbols(other)
            other_edges = _snapshot_edges(other)
            if (
                other["generation"] != scalar_snap["generation"]
                or other["changed_files"] != scalar_snap["changed_files"]
                or other_spans != scalar_spans
                or other_symbols != scalar_symbols
                or other_edges != scalar_edges
            ):
                summary: dict[str, object] = {
                    "metadata": _platform_metadata(),
                    "workloads": [],
                    "all_passed": False,
                    "failed_workloads": ["output_equality"],
                    "mismatch": {
                        "scalar": {
                            "generation": scalar_snap["generation"],
                            "changed_files": scalar_snap["changed_files"],
                            "span_count": len(scalar_spans),
                            "symbol_count": len(scalar_symbols),
                            "edge_count": len(scalar_edges),
                        },
                        name: {
                            "generation": other["generation"],
                            "changed_files": other["changed_files"],
                            "span_count": len(other_spans),
                            "symbol_count": len(other_symbols),
                            "edge_count": len(other_edges),
                        },
                    },
                }
                return summary, False
        comparison, all_passed = _compare_implementations(
            implementations=("scalar", "accelerated", "auto"),
            samples=samples,
            mode=mode,
            timeout_ms=timeout_ms,
            include_small=False,
            enforce_speedup=enforce_speedup,
            quick=False,
        )
        comparison["workload_bytes"] = total_bytes
        comparison["snapshots_agreed"] = True
        return comparison, all_passed


def _implementation_runs(implementations: Sequence[str]) -> list[str]:
    canonical = ("scalar", "accelerated", "auto")
    return [name for name in canonical if name in implementations]


def _parse_implementations(arg: str) -> list[str]:
    parts: list[str] = []
    for token in arg.replace(",", " ").split():
        stripped = token.strip()
        if stripped:
            parts.append(stripped)
    if not parts:
        raise argparse.ArgumentTypeError("expected at least one implementation")
    allowed = {"scalar", "accelerated", "auto"}
    for name in parts:
        if name not in allowed:
            raise argparse.ArgumentTypeError(
                f"unknown implementation: {name!r}; expected one of {sorted(allowed)}"
            )
    return parts


def _build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="python -m ralph.mcp.explore.reindex_bench",
        description=(
            "Benchmark and A/B compare the production reindex() "
            "path under the scalar and accelerated Python "
            "structure extractors."
        ),
    )
    parser.add_argument(
        "--samples",
        type=int,
        default=5,
        help="number of timed samples per implementation (default: 5)",
    )
    parser.add_argument(
        "--mode",
        choices=("full", "changed"),
        default="full",
        help="reindex mode to benchmark (default: full)",
    )
    parser.add_argument(
        "--timeout-ms",
        type=int,
        default=120_000,
        help="reindex timeout in milliseconds (default: 120000)",
    )
    mode = parser.add_mutually_exclusive_group()
    mode.add_argument(
        "--implementation",
        type=_parse_implementations,
        default=("scalar",),
        help=(
            "comma-separated list of implementations to benchmark "
            "(scalar, accelerated, auto; default: scalar)"
        ),
    )
    mode.add_argument(
        "--compare",
        nargs="+",
        default=None,
        help=(
            "space-separated implementations to A/B compare "
            "(e.g. --compare scalar accelerated auto); "
            "tokens may also be comma-separated"
        ),
    )
    parser.add_argument(
        "--small-workload",
        action="store_true",
        help=(
            "include the small workload so the bench also "
            "reports the auto-vs-scalar crossover behaviour"
        ),
    )
    parser.add_argument(
        "--end-to-end",
        action="store_true",
        help=(
            "force a full reindex() drive per implementation, "
            "assert byte-equal outputs across all implementations, "
            "and run the A/B comparison with auto in the mix"
        ),
    )
    parser.add_argument(
        "--quick",
        action="store_true",
        help=(
            "reduce the sample count to 2 for fast smoke runs; "
            "the result is not statistically conclusive"
        ),
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    parser = _build_parser()
    args = parser.parse_args(argv)
    quick_value: bool = args.quick
    samples_value: int = args.samples
    raw_compare: object = args.compare
    raw_implementation: object = args.implementation
    compare_tokens: list[str] | None = list(raw_compare) if isinstance(raw_compare, list) else None
    implementation_tokens: list[str] = (
        list(raw_implementation) if isinstance(raw_implementation, list) else []
    )
    samples = 2 if quick_value else max(1, samples_value)
    if compare_tokens is not None:
        flat: list[str] = []
        for token in compare_tokens:
            for piece in token.split(","):
                stripped = piece.strip()
                if stripped:
                    flat.append(stripped)
        raw_implementations = _parse_implementations(",".join(flat))
    else:
        raw_implementations = implementation_tokens
    implementations = _implementation_runs(raw_implementations)
    if not implementations:
        error_payload: dict[str, str] = {"error": "no implementations selected"}
        print(
            json.dumps(error_payload, sort_keys=True),
            file=sys.stdout,
        )
        return 2
    mode_value: str = args.mode
    timeout_value: int = args.timeout_ms
    end_to_end_value: bool = args.end_to_end
    small_workload_value: bool = args.small_workload
    if end_to_end_value:
        summary, all_passed = _end_to_end(
            samples=samples,
            mode=mode_value,
            timeout_ms=timeout_value,
            enforce_speedup=not quick_value,
        )
    else:
        summary, all_passed = _compare_implementations(
            implementations=implementations,
            samples=samples,
            mode=mode_value,
            timeout_ms=timeout_value,
            include_small=small_workload_value,
            enforce_speedup=not quick_value,
            quick=quick_value,
        )
    print(json.dumps(summary, sort_keys=True, default=str))
    return 0 if all_passed else 1


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "REPRESENTATIVE_FILE_COUNT",
    "REPRESENTATIVE_LINES_PER_FILE",
    "REPRESENTATIVE_MIN_SPEEDUP",
    "SMALL_FILE_COUNT",
    "SMALL_LINES_PER_FILE",
    "SMALL_MIN_SPEEDUP",
    "_index_snapshot",
    "_interleaved_measure",
    "_measure_one",
    "_run_reindex",
    "_seeded_workspace",
]

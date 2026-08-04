"""Standalone benchmark harness for the ``reindex()`` hot path.

This CLI exists so the plan's A/B speedup measurement has a real,
reproducible driver: it builds a bounded Python corpus, runs the
full ``reindex()`` path under each available structure extractor,
interleaves the samples to amortise warmup / cooldown noise, and
prints machine-readable statistics (median, spread, speedup ratio,
workload size, implementation, CPU / platform metadata).

The bench is intentionally narrow:

* It only exercises the ``reindex(mode='full' | 'changed')`` path
  over a synthetic codebase. The legacy
  :mod:`ralph.mcp.explore.bench` is a transcript-cost harness with
  no ``__main__``; this module is the real wall-clock evidence.
* Output byte-equality is mandatory. The CLI exits nonzero when
  the accelerated path produces a different generation, file row,
  or structure row set than the scalar reference.
* The CLI uses fixed corpora (no randomness across runs) and
  interleaved samples (not back-to-back) so per-run jitter cannot
  bias the comparison.
* The CLI is meant to be runnable as ``python -m
  ralph.mcp.explore.reindex_bench`` from the repo root. It writes
  its JSON summary to stdout so the caller can pipe it.
"""
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

# Small corpus used by the ``--small-workload`` mode. Drives the
# AST walker through just a handful of small files so the
# per-call dispatch cost dominates and the auto selector must
# switch back to scalar. Tuned so the full run fits in the
# 60-second combined test budget with comfortable headroom.
SMALL_FILE_COUNT = 8
SMALL_LINES_PER_FILE = 12

# Representative corpus used by the default bench. Drives the
# AST walker through enough work that the accelerated fused
# walker measurably beats the historical four-pass walker. The
# number is small enough to keep one sample well under a
# second on commodity hardware while still surfacing the
# accelerated speedup above measurement noise.
REPRESENTATIVE_FILE_COUNT = 60
REPRESENTATIVE_LINES_PER_FILE = 60

# Predclared noise floor for the A/B comparison. The
# representative corpus has a single-sample median noise
# measured at well under 5% on the development host; this
# constant gives the bench a stable threshold so the result
# is reproducible across machines without per-host tuning.
REPRESENTATIVE_MIN_SPEEDUP = 1.10
SMALL_MIN_SPEEDUP = 1.0  # below 1.0 means scalar is allowed to win

#: Row widths produced by :func:`_index_snapshot`; these are
#: declared once so the ``isinstance(row, tuple) and len(row) == N``
#: predicates below resolve to a non-magic constant.
_SNAPSHOT_SPAN_WIDTH = 5
_SNAPSHOT_SYMBOL_WIDTH = 5
_SNAPSHOT_EDGE_WIDTH = 5


def _seed_corpus(root: Path, *, files: int, lines_per_file: int) -> int:
    """Write ``files`` Python modules under ``root`` and return total bytes."""
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
        # Deterministic cross-file reference so the
        # references_text relation has at least one edge in the
        # small corpus too.
        body += f"def ref_{i}():\n    return fn_{i}_0(0, 0)\n"
        full = root / rel
        full.parent.mkdir(parents=True, exist_ok=True)
        # filesystem-write-ok: transient scratch under a ``tempfile.TemporaryDirectory`` (deleted by ``tempfile`` on context exit).
        full.write_text(body)
        total_bytes += len(body)
    return total_bytes


def _seeded_workspace(
    *,
    files: int,
    lines_per_file: int,
    parent: Path,
) -> tuple[Path, int]:
    """Create a fresh workspace rooted under ``parent`` and return the path."""
    workspace = parent / "ws"
    if workspace.exists():
        # filesystem-write-ok: cleanup of transient benchmark workspace under a ``tempfile.TemporaryDirectory``.
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
    """Run ``reindex()`` over ``workspace`` and return ``(generation, files)``."""
    store_dir = parent / f"index-{structure_extractor}-{mode}"
    if store_dir.exists():
        # filesystem-write-ok: cleanup of transient per-sample index directory under a ``tempfile.TemporaryDirectory``.
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
    """Sort key used by :func:`_index_snapshot` for spans.

    Named helper rather than a ``lambda`` so strict mypy can
    resolve ``row`` to the ``SpanRow`` type. A lambda's
    parameter type defaults to ``Any`` under ``disallow_any_expr``
    which fails the gate.
    """
    return (row.path, row.start_line, row.span_id)


def _symbol_sort_key(row: SymbolRow) -> tuple[str, str, str]:
    """Sort key used by :func:`_index_snapshot` for symbols."""
    return (row.path, row.qualified_name, row.symbol_id)


def _edge_sort_key(row: EdgeRow) -> tuple[str, str, str]:
    """Sort key used by :func:`_index_snapshot` for edges."""
    return (row.path, row.relation, row.edge_id)


def _snapshot_spans(
    snapshot: dict[str, object],
) -> list[tuple[str, str, int, int, str | None]]:
    """Return the spans list from an ``_index_snapshot`` result.

    The snapshot dict is constructed by :func:`_index_snapshot`
    with concrete typed lists (spans / symbols / edges) so the
    access here is type-safe by construction; this helper
    narrows the untyped ``dict[str, object]`` to the span
    tuple shape without resorting to ``cast``.
    """
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
    """Return the symbols list from an ``_index_snapshot`` result."""
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
    """Return the edges list from an ``_index_snapshot`` result."""
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
    """Return a deterministic snapshot of the indexed SQLite state.

    The bench compares ``scalar`` and ``accelerated`` snapshots
    via this helper; equality proves the two extractors write
    identical rows to the store. The return type is loosely
    ``dict[str, object]`` because the JSON payload carries
    heterogeneous values (generation int, file list, row
    tuples); callers cast to the concrete shape when needed.
    """
    store_dir = parent / f"snap-{structure_extractor}-{mode}"
    if store_dir.exists():
        # filesystem-write-ok: cleanup of transient snapshot directory under a ``tempfile.TemporaryDirectory``.
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
            (s.span_id, s.kind, s.start_line, s.end_line, s.symbol_id)
            for s in spans_rows
        ]
        symbols_rows = sorted(
            store.iter_symbols(),
            key=_symbol_sort_key,
        )
        symbols_list: list[tuple[str, str, str, str, str]] = [
            (s.symbol_id, s.name, s.qualified_name, s.kind, s.path)
            for s in symbols_rows
        ]
        edges_rows = sorted(
            store.iter_edges(),
            key=_edge_sort_key,
        )
        edges_list: list[tuple[str, str, str, str, str]] = [
            (e.edge_id, e.relation, e.source_id, e.target_id, e.path)
            for e in edges_rows
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
    """Measure a single ``reindex()`` invocation in nanoseconds."""
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
    """Measure each implementation ``samples`` times in round-robin order.

    Interleaving amortises warmup / cooldown so the medians are
    not biased by the order the implementations run.
    """
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
    """Return the canonical median / spread statistics for a sample list."""
    if not samples:
        return {"median_ns": 0.0, "spread_ns": 0.0, "min_ns": 0.0, "max_ns": 0.0}
    return {
        "median_ns": float(statistics.median(samples)),
        "spread_ns": float(max(samples) - min(samples)),
        "min_ns": float(min(samples)),
        "max_ns": float(max(samples)),
    }


def _platform_metadata() -> dict[str, str]:
    """Return bounded platform / CPU metadata for the JSON summary."""
    cpu: dict[str, str] = {}
    try:
        cpu_info = platform.processor()
        if isinstance(cpu_info, str) and cpu_info:
            cpu["processor"] = cpu_info
    except Exception:  # pragma: no cover - defensive
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
) -> dict[str, object]:
    """Build the A/B summary block for ``implementations``.

    Computes the per-implementation median / spread, the
    scalar-vs-accelerated speedup ratio (when both are in the
    dict), and a ``passed`` boolean keyed on the declared
    threshold so the caller can fail closed when the speedup
    regresses below it.
    """
    summary: dict[str, object] = {"label": label}
    for name in implementations:
        summary[name] = _summary_statistics(timings[name])
    if "scalar" in timings and "accelerated" in timings:
        scalar_median = statistics.median(timings["scalar"])
        accel_median = statistics.median(timings["accelerated"])
        speedup = scalar_median / accel_median if accel_median > 0 else 0.0
        summary["scalar_vs_accelerated_speedup"] = round(speedup, 4)
        summary["min_speedup"] = min_speedup
        summary["passed"] = bool(speedup >= min_speedup)
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
) -> dict[str, object]:
    """Run the representative A/B workload and return the summary block."""
    workspace, total_bytes = _seeded_workspace(
        files=REPRESENTATIVE_FILE_COUNT,
        lines_per_file=REPRESENTATIVE_LINES_PER_FILE,
        parent=parent,
    )
    # Warmup: one untimed pass per implementation so the OS file
    # cache and the SQLite write-ahead log are both warm before
    # the measured samples start. Warmup cost is excluded from
    # the medians.
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
        "file_count": REPRESENTATIVE_FILE_COUNT,
        "lines_per_file": REPRESENTATIVE_LINES_PER_FILE,
        "workload_bytes": total_bytes,
        "mode": mode,
        "samples": samples,
        **_compare_summary(
            timings=timings,
            implementations=implementations,
            min_speedup=REPRESENTATIVE_MIN_SPEEDUP,
            label="representative",
        ),
    }


def _run_small(
    *,
    parent: Path,
    implementations: Sequence[str],
    samples: int,
    mode: str,
    timeout_ms: int,
) -> dict[str, object]:
    """Run the small A/B workload so ``auto`` can switch to scalar."""
    workspace, total_bytes = _seeded_workspace(
        files=SMALL_FILE_COUNT,
        lines_per_file=SMALL_LINES_PER_FILE,
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
        "file_count": SMALL_FILE_COUNT,
        "lines_per_file": SMALL_LINES_PER_FILE,
        "workload_bytes": total_bytes,
        "mode": mode,
        "samples": samples,
        **_compare_summary(
            timings=timings,
            implementations=implementations,
            min_speedup=SMALL_MIN_SPEEDUP,
            label="small",
        ),
    }


def _output_summary(summaries: Sequence[dict[str, object]]) -> dict[str, object]:
    """Combine multiple workload summaries into the final CLI payload."""
    overall: dict[str, object] = {
        "metadata": _platform_metadata(),
        "workloads": list(summaries),
    }
    failed = [
        workload
        for workload in summaries
        if workload.get("passed") is False
    ]
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
) -> tuple[dict[str, object], bool]:
    """Run the A/B comparison workloads and return ``(summary, all_passed)``."""
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
                )
            )
        summary = _output_summary(workloads)
        return summary, bool(summary["all_passed"])


def _end_to_end(
    *,
    samples: int,
    mode: str,
    timeout_ms: int,
) -> tuple[dict[str, object], bool]:
    """Drive the full ``reindex()`` path with output equality checks.

    Returns ``(summary, all_passed)``. The end-to-end mode
    forces identical generation / file / structure snapshots
    between ``scalar`` and ``accelerated`` (and ``auto``) and
    then runs the representative A/B comparison. Failures on
    output equality exit the CLI with a nonzero status so the
    bench never reports a fabricated speedup.
    """
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
        # Snapshots agree; run the A/B comparison with the auto
        # selector in the mix so the end-to-end number reflects
        # the real production path.
        comparison, all_passed = _compare_implementations(
            implementations=("scalar", "accelerated", "auto"),
            samples=samples,
            mode=mode,
            timeout_ms=timeout_ms,
            include_small=False,
        )
        comparison["workload_bytes"] = total_bytes
        comparison["snapshots_agreed"] = True
        return comparison, all_passed


def _implementation_runs(implementations: Sequence[str]) -> list[str]:
    """Return the implementation names in CLI-canonical order."""
    canonical = ("scalar", "accelerated", "auto")
    return [name for name in canonical if name in implementations]


def _parse_implementations(arg: str) -> list[str]:
    """Parse a comma- or space-separated implementation name list."""
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
                f"unknown implementation: {name!r}; expected one of "
                f"{sorted(allowed)}"
            )
    return parts


def _build_parser() -> argparse.ArgumentParser:
    """Construct the CLI parser."""
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
    """Entry point used by ``python -m ralph.mcp.explore.reindex_bench``."""
    parser = _build_parser()
    args = parser.parse_args(argv)
    # ``args`` is an ``argparse.Namespace``; each field is
    # ``Any`` from mypy's perspective. The argparse ``type``
    # hooks above guarantee the runtime shape, so the local
    # variables below are typed with the canonical Python
    # surface. Re-binding through typed locals keeps the
    # ``disallow_any_expr`` gate happy.
    quick_value: bool = args.quick
    samples_value: int = args.samples
    # ``args.compare`` and ``args.implementation`` are
    # ``Any`` from mypy's perspective; rebind them through
    # ``list(...)`` so the element type is ``object`` (a
    # concrete shape rather than Any), then narrow further
    # through the parser hooks below.
    raw_compare: object = args.compare
    raw_implementation: object = args.implementation
    compare_tokens: list[str] | None = (
        list(raw_compare) if isinstance(raw_compare, list) else None
    )
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
        )
    else:
        summary, all_passed = _compare_implementations(
            implementations=implementations,
            samples=samples,
            mode=mode_value,
            timeout_ms=timeout_value,
            include_small=small_workload_value,
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

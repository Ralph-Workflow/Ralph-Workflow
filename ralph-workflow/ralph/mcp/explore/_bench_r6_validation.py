"""Validation helpers and canonical metric definitions for R6 benchmarks.

Split from ``_bench_r6_metrics`` so both modules stay under the
repository file-size limit.
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

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
            errors.append(f"row has invalid disposition {disposition!r}: {row}")
    return tuple(errors)


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

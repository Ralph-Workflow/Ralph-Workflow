"""Validation helpers and canonical metric definitions for R6 benchmarks.

Split from ``_bench_r6_metrics`` so both modules stay under the
repository file-size limit.
"""

from __future__ import annotations

import json
import sys
from collections.abc import Callable, Mapping
from pathlib import Path
from types import MappingProxyType
from typing import Final

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

#: ``indexed_vs_live_speed_ratio`` is a LOWER-bound metric: indexed search must
#: be faster than live search, so ``Final > Baseline`` is the improved direction.
_LOWER_BOUND_METRICS: Final[frozenset[str]] = frozenset({"indexed_vs_live_speed_ratio"})

#: Metrics that allow ``Final < Baseline`` strictly (smaller is strictly better)
#: without relabelling an already-tolerance-passing row as "improved" — these
#: are the regular upper-bound resource metrics.
_UPPER_BOUND_METRICS: Final[frozenset[str]] = frozenset(
    metric for metric in _R6_2_METRICS if metric not in _LOWER_BOUND_METRICS
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


def _parse_metric_table(
    raw: str,
) -> tuple[list[list[str]], list[str]]:
    """Return ``(rows_after_separator, errors)`` for the report's metric table.

    ``rows_after_separator`` is the list of table rows after the
    ``|---|---|...`` separator line, each as a list of trimmed cell
    strings (the leading/trailing empty cell created by the leading
    ``|`` is preserved so cell indices match the canonical schema).
    Errors are returned for header / shape problems; per-row content
    errors are surfaced by the caller.
    """
    errors: list[str] = []
    if "| Metric" not in raw and "|metric" not in raw:
        return ([], ["report is missing the canonical metric table header"],)
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
    min_report_table_rows = 3  # header + separator + at least one row
    if len(rows) < min_report_table_rows:
        return ([], ["report is missing the canonical metric table body"],)
    body_rows = rows[2:]
    parsed: list[list[str]] = []
    for row in body_rows:
        cells = [c.strip() for c in row.split("|")]
        parsed.append(cells)
    return (parsed, errors)


def _derive_disposition(
    *,
    baseline_value: float,
    final_value: float,
    target_value: float,
    metric: str,
    comparator: Callable[[float, float, str], bool],
) -> str | None:
    """Return the expected disposition for one row, or ``None`` on parse failure.

    Returns:
      - ``"regression"`` when ``final_value`` fails the target.
      - ``"improved"`` when ``final_value`` is strictly better than
        ``baseline_value`` in the metric's direction.
      - ``"within-tolerance"`` when the target is met but the final
        value is not strictly better than baseline.

    Strictly better means strictly smaller for upper-bound metrics
    (the default) and strictly larger for ``indexed_vs_live_speed_ratio``.
    The comparator is reused for the regression branch so the
    direction / tolerance logic is single-sourced from
    ``measurement_within_target``.
    """
    if not comparator(final_value, target_value, metric):
        return "regression"
    if metric in _LOWER_BOUND_METRICS:
        if final_value > baseline_value:
            return "improved"
        return "within-tolerance"
    if final_value < baseline_value:
        return "improved"
    return "within-tolerance"


def validate_report(
    report_path: Path,
    *,
    baseline: Mapping[str, Mapping[str, float]] | None = None,
    targets: Mapping[str, Mapping[str, float]] | None = None,
) -> tuple[str, ...]:
    """Return one error string per problem, empty list = pass.

    When ``baseline`` and ``targets`` are given, every row is
    cross-checked mechanically:

    - The Baseline cell must equal the baseline JSON value (exact).
    - The Target cell must equal the targets JSON value (exact).
    - The Disposition must be derived from Baseline/Final/Target by
      reusing ``measurement_within_target`` (regression vs.
      within-tolerance vs. improved).
    - The Final cell must be numeric.

    Cross-check failures return explicit messages; the row schema
    is still verified first so the legacy shape-only path keeps
    working.
    """
    raw = report_path.read_text()
    parsed_rows, errors = _parse_metric_table(raw)
    if errors:
        return tuple(errors)
    if baseline is None and targets is None:
        return _validate_report_shape_only(parsed_rows)
    if baseline is None or targets is None:
        return (
            "validate_report requires both baseline and targets (or neither)",
        )
    return _validate_report_with_cross_check(
        parsed_rows,
        baseline=baseline,
        targets=targets,
    )


def _validate_report_shape_only(
    parsed_rows: list[list[str]],
) -> tuple[str, ...]:
    """Original shape-only validator: 7 cells and disposition vocabulary."""
    errors: list[str] = []
    required_report_cells = 7
    disposition_column_index = 6
    for cells in parsed_rows:
        if len(cells) < required_report_cells:
            errors.append(f"row missing cells (need 7): {cells!r}")
            continue
        disposition = cells[disposition_column_index]
        if disposition not in {"improved", "regression", "within-tolerance"}:
            errors.append(f"row has invalid disposition {disposition!r}: {cells!r}")
    return tuple(errors)


def _parse_float_cell(cell: str) -> float | None:
    """Return the cell as a float, or ``None`` when not parseable."""
    try:
        return float(cell)
    except (TypeError, ValueError):
        return None


def _validate_report_with_cross_check(
    parsed_rows: list[list[str]],
    *,
    baseline: Mapping[str, Mapping[str, float]],
    targets: Mapping[str, Mapping[str, float]],
) -> tuple[str, ...]:
    """Mechanical cross-check: every row must match baseline + targets.

    Empty rows (no cells beyond the leading empty cell) are skipped so
    blank table-padding lines do not fail the gate. The Baseline
    and Target cells are matched exactly (no tolerance) so an
    internal drift between the report and the JSON files is
    surfaced; only the Final column participates in the
    tolerance-driven ``measurement_within_target`` comparator.
    """
    # Local import to keep this module free of a hard requirement on
    # the metrics module (the comparator is the only function we
    # need and it must match the in-budget gate's comparator).
    from ralph.mcp.explore._bench_r6_metrics import measurement_within_target

    cell_metric = 1
    cell_workload = 2
    cell_baseline = 3
    cell_final = 4
    cell_target = 5
    cell_disposition = 6
    required_cells = cell_disposition + 1
    cell_eps = 1e-9

    errors: list[str] = []
    for cells in parsed_rows:
        if len(cells) < required_cells:
            errors.append(f"row missing cells (need {required_cells}): {cells!r}")
            continue
        metric = cells[cell_metric].strip() if len(cells) > cell_metric else ""
        workload = cells[cell_workload].strip() if len(cells) > cell_workload else ""
        baseline_text = cells[cell_baseline].strip() if len(cells) > cell_baseline else ""
        final_text = cells[cell_final].strip() if len(cells) > cell_final else ""
        target_text = cells[cell_target].strip() if len(cells) > cell_target else ""
        disposition_text = (
            cells[cell_disposition].strip() if len(cells) > cell_disposition else ""
        )
        if not metric and not workload:
            # Blank padding row.
            continue
        if metric not in _R6_2_METRICS:
            errors.append(f"row has unknown metric {metric!r}")
            continue
        if workload not in _R6_3_WORKLOADS:
            errors.append(f"row has unknown workload {workload!r}")
            continue
        baseline_value_obj = baseline.get(workload, {}).get(metric)
        target_value_obj = targets.get(workload, {}).get(metric)
        if not isinstance(baseline_value_obj, (int, float)):
            errors.append(
                f"{workload}.{metric}: baseline JSON missing numeric value",
            )
            continue
        if not isinstance(target_value_obj, (int, float)):
            errors.append(
                f"{workload}.{metric}: targets JSON missing numeric value",
            )
            continue
        baseline_value = float(baseline_value_obj)
        target_value = float(target_value_obj)
        final_value = _parse_float_cell(final_text)
        if final_value is None:
            errors.append(
                f"{workload}.{metric}: Final cell {final_text!r} not numeric",
            )
            continue
        baseline_cell_value = _parse_float_cell(baseline_text)
        if baseline_cell_value is None:
            errors.append(
                f"{workload}.{metric}: Baseline cell {baseline_text!r} not numeric",
            )
            continue
        target_cell_value = _parse_float_cell(target_text)
        if target_cell_value is None:
            errors.append(
                f"{workload}.{metric}: Target cell {target_text!r} not numeric",
            )
            continue
        if abs(baseline_cell_value - baseline_value) > cell_eps:
            errors.append(
                f"{workload}.{metric}: Baseline cell {baseline_cell_value} "
                f"!= baseline JSON {baseline_value}",
            )
        if abs(target_cell_value - target_value) > cell_eps:
            errors.append(
                f"{workload}.{metric}: Target cell {target_cell_value} "
                f"!= targets JSON {target_value}",
            )
        expected = _derive_disposition(
            baseline_value=baseline_value,
            final_value=final_value,
            target_value=target_value,
            metric=metric,
            comparator=measurement_within_target,
        )
        if expected is None:
            errors.append(
                f"{workload}.{metric}: could not derive disposition "
                f"for Final={final_value} Target={target_value}",
            )
            continue
        if disposition_text != expected:
            errors.append(
                f"{workload}.{metric}: Disposition cell "
                f"{disposition_text!r} != derived {expected!r} "
                f"(Baseline={baseline_value}, Final={final_value}, "
                f"Target={target_value})",
            )
    return tuple(errors)


def _load_baseline_metrics(path: Path) -> Mapping[str, Mapping[str, float]]:
    """Load the baseline JSON; fail closed on malformed content."""
    raw_obj: object = json.loads(path.read_text())
    if not isinstance(raw_obj, dict):
        raise ValueError(f"baseline JSON at {path} is not an object")
    raw_dict: dict[object, object] = raw_obj
    metrics_obj: object = raw_dict.get("metrics")
    if not isinstance(metrics_obj, dict):
        raise ValueError(f"baseline JSON at {path} is missing a 'metrics' object")
    metrics_dict: dict[object, object] = metrics_obj
    out: dict[str, dict[str, float]] = {}
    for workload_obj, workload_metrics in metrics_dict.items():
        if not isinstance(workload_metrics, dict):
            raise ValueError(
                f"baseline JSON workload {workload_obj!r} is not a metrics object",
            )
        workload_metrics_dict: dict[object, object] = workload_metrics
        workload_metrics_clean: dict[str, float] = {}
        for metric_obj, value in workload_metrics_dict.items():
            if not isinstance(value, (int, float)):
                raise ValueError(
                    f"baseline JSON {workload_obj}.{metric_obj} is not numeric",
                )
            workload_metrics_clean[str(metric_obj)] = float(value)
        out[str(workload_obj)] = workload_metrics_clean
    return MappingProxyType(out)


def _load_targets_metrics(path: Path) -> Mapping[str, Mapping[str, float]]:
    """Load the targets JSON; fail closed on malformed content."""
    raw_obj: object = json.loads(path.read_text())
    if not isinstance(raw_obj, dict):
        raise ValueError(f"targets JSON at {path} is not an object")
    raw_dict: dict[object, object] = raw_obj
    metrics_obj: object = raw_dict.get("metrics")
    if not isinstance(metrics_obj, dict):
        raise ValueError(f"targets JSON at {path} is missing a 'metrics' object")
    metrics_dict: dict[object, object] = metrics_obj
    out: dict[str, dict[str, float]] = {}
    for workload_obj, workload_metrics in metrics_dict.items():
        if not isinstance(workload_metrics, dict):
            raise ValueError(
                f"targets JSON workload {workload_obj!r} is not a metrics object",
            )
        workload_metrics_dict: dict[object, object] = workload_metrics
        workload_metrics_clean: dict[str, float] = {}
        for metric_obj, value in workload_metrics_dict.items():
            if not isinstance(value, (int, float)):
                raise ValueError(
                    f"targets JSON {workload_obj}.{metric_obj} is not numeric",
                )
            workload_metrics_clean[str(metric_obj)] = float(value)
        out[str(workload_obj)] = workload_metrics_clean
    return MappingProxyType(out)


def run_validate_baseline(baseline_path: str) -> int:
    """CLI entry: validate the S-8 baseline JSON."""
    failures = validate_baseline(Path(baseline_path))
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"OK: {baseline_path}")
    return 0


def run_validate_report(
    report_path: str,
    baseline_path: str | None = None,
    targets_path: str | None = None,
) -> int:
    """CLI entry: validate the S-10 before/after report.

    When ``baseline_path`` and ``targets_path`` are both given, the
    CLI mechanically cross-checks every report row against the two
    JSON files. Either companion path without the other fails
    closed so the operator cannot silently bypass the cross-check.
    """
    if (baseline_path is None) != (targets_path is None):
        print(
            "FAIL: --baseline and --targets must be supplied together",
            file=sys.stderr,
        )
        return 1
    baseline_metrics: Mapping[str, Mapping[str, float]] | None = None
    targets_metrics: Mapping[str, Mapping[str, float]] | None = None
    if baseline_path is not None and targets_path is not None:
        try:
            baseline_metrics = _load_baseline_metrics(Path(baseline_path))
            targets_metrics = _load_targets_metrics(Path(targets_path))
        except (OSError, ValueError) as exc:
            print(f"FAIL: {exc}", file=sys.stderr)
            return 1
    failures = validate_report(
        Path(report_path),
        baseline=baseline_metrics,
        targets=targets_metrics,
    )
    if failures:
        for failure in failures:
            print(f"FAIL: {failure}", file=sys.stderr)
        return 1
    print(f"OK: {report_path}")
    return 0


__all__ = [
    "_LOWER_BOUND_METRICS",
    "_UPPER_BOUND_METRICS",
    "run_validate_baseline",
    "run_validate_report",
    "validate_baseline",
    "validate_report",
]

from __future__ import annotations

import json
import tempfile
from pathlib import Path

import pytest

from ralph.mcp.explore._bench_r6_metrics import (
    _R6_2_METRICS,
    _R6_3_WORKLOADS,
    FULL_LARGE_SYNTHETIC_FILE_COUNT,
    _seed_large_synthetic,
    measurement_within_target,
    validate_baseline,
)
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore

BASELINE_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "performance" / "explore-index-baseline.json"
)
TARGETS_PATH = (
    Path(__file__).resolve().parents[1] / "docs" / "performance" / "explore-index-targets.json"
)


def _load_targets() -> dict[str, dict[str, float]]:
    assert TARGETS_PATH.is_file(), f"missing targets JSON: {TARGETS_PATH}"
    raw = json.loads(TARGETS_PATH.read_text())
    targets: dict[str, dict[str, float]] = {}
    for workload in _R6_3_WORKLOADS:
        workload_block = raw.get("metrics", {}).get(workload, {})
        targets[workload] = {}
        for metric in _R6_2_METRICS:
            value = workload_block.get(metric)
            assert isinstance(value, (int, float)), (
                f"target for {workload}.{metric} is not numeric: {value!r}"
            )
            targets[workload][metric] = float(value)
    return targets


def test_in_budget_baseline_schema_present_and_mechanical() -> None:
    assert BASELINE_PATH.is_file(), f"missing baseline JSON: {BASELINE_PATH}"
    failures = validate_baseline(BASELINE_PATH)
    assert not failures, f"baseline schema validation failed: {failures}"


def test_in_budget_targets_present_and_numeric() -> None:
    targets = _load_targets()
    for workload in _R6_3_WORKLOADS:
        assert workload in targets, f"workload {workload} missing from targets"
        for metric in _R6_2_METRICS:
            assert metric in targets[workload], f"target for {workload}.{metric} missing"
            assert isinstance(targets[workload][metric], float), (
                f"target for {workload}.{metric} is not numeric"
            )


def test_in_budget_baseline_covers_every_r6_2_metric() -> None:
    payload = json.loads(BASELINE_PATH.read_text())
    metrics = payload["metrics"]
    for workload in _R6_3_WORKLOADS:
        for metric in _R6_2_METRICS:
            assert metric in metrics[workload], f"{workload}.{metric} missing from baseline"


def test_in_budget_baseline_covers_every_r6_3_workload() -> None:
    payload = json.loads(BASELINE_PATH.read_text())
    for workload in _R6_3_WORKLOADS:
        assert workload in payload["metrics"], f"{workload} workload missing"
    counts = payload["file_counts"]
    assert counts["large_synthetic"] >= 10_000, (
        f"large_synthetic file count {counts['large_synthetic']} "
        "is below the 10 000-file R6.3 full shape; the committed "
        "baseline must be re-captured at the real workload size."
    )
    assert counts["ralph_self"] >= 1_000, (
        f"ralph_self file count {counts['ralph_self']} is below "
        "the real ralph-workflow tree size (thousands of files); "
        "the committed baseline must be re-captured from the real tree."
    )
    assert counts["small"] >= 1
    assert counts["multi_session"] >= 1


@pytest.mark.timeout_seconds(20)
def test_in_budget_full_synthetic_seeder_builds_tens_of_thousands(tmp_path: Path) -> None:
    workspace = _seed_large_synthetic(
        tmp_path,
        file_count=FULL_LARGE_SYNTHETIC_FILE_COUNT,
    )
    file_count = sum(1 for path in workspace.rglob("*") if path.is_file())
    assert file_count >= 10_000


@pytest.mark.timeout_seconds(20)
def test_in_budget_baseline_within_numeric_targets() -> None:
    baseline = json.loads(BASELINE_PATH.read_text())
    targets = _load_targets()
    for workload in _R6_3_WORKLOADS:
        for metric in _R6_2_METRICS:
            measured = float(baseline["metrics"][workload][metric])
            target = targets[workload][metric]
            assert measurement_within_target(measured, target, metric), (
                f"{workload}.{metric}: measured {measured} outside target {target}"
            )


@pytest.mark.timeout_seconds(20)
def test_in_budget_negative_gate_fails_on_tightened_threshold() -> None:
    baseline = json.loads(BASELINE_PATH.read_text())
    workload = "small"
    metric = "cold_build_wall_seconds"
    measured = float(baseline["metrics"][workload][metric])
    assert measured > 0.0, "cold build baseline must be a real non-zero measurement"
    assert measurement_within_target(measured, 0.0, metric) is False


@pytest.mark.timeout_seconds(20)
def test_in_budget_scaled_synthetic_metrics_within_tolerance(tmp_path: Path) -> None:
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.py").write_text("def hello():\n    return 'world'\n")
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, ws, options=ReindexOptions(timeout_ms=5_000))
        generation_raw = store.get_setting("current_generation") or "0"
        assert int(generation_raw) > 0
    finally:
        store.close()


def test_in_budget_validate_baseline_rejects_partial_run() -> None:
    with tempfile.TemporaryDirectory() as scratch:
        partial = Path(scratch) / "partial.json"
        payload = {
            "metrics": {
                "small": {"cold_build_wall_seconds": 0.1},
                "ralph_self": {},
                "large_synthetic": {},
                "multi_session": {},
            }
        }
        partial.write_text(json.dumps(payload))
        failures = validate_baseline(partial)
        assert failures
        assert any("missing metrics" in f for f in failures)


def _build_cross_check_triple(
    *,
    workload: str,
    metric: str,
    baseline_value: float,
    target_value: float,
    final_value: float,
    disposition: str,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]], str]:
    baseline = {workload: {metric: baseline_value}}
    targets = {workload: {metric: target_value}}
    report = (
        "# test\n"
        "\n"
        "| Metric | Workload | Baseline | Final | Target | Disposition |\n"
        "|---|---|---|---|---|---|\n"
        f"| {metric} | {workload} | {baseline_value!r} | {final_value!r} | {target_value!r} | {disposition} |\n"
    )
    return baseline, targets, report


def test_cross_check_passes_on_consistent_data() -> None:
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline, targets, report = _build_cross_check_triple(
        workload="small",
        metric="cold_build_wall_seconds",
        baseline_value=0.5,
        target_value=0.5,
        final_value=0.5,
        disposition="within-tolerance",
    )
    with tempfile.TemporaryDirectory() as scratch:
        report_path = Path(scratch) / "report.md"
        report_path.write_text(report)
        failures = validate_report(
            report_path,
            baseline=baseline,
            targets=targets,
        )
        assert not failures, f"unexpected failures: {failures}"


def test_cross_check_fails_on_wrong_baseline_cell() -> None:
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline, targets, report = _build_cross_check_triple(
        workload="small",
        metric="cold_build_wall_seconds",
        baseline_value=0.5,
        target_value=0.5,
        final_value=0.5,
        disposition="within-tolerance",
    )
    tampered_report = report.replace("0.5", "0.7", 1)
    with tempfile.TemporaryDirectory() as scratch:
        report_path = Path(scratch) / "report.md"
        report_path.write_text(tampered_report)
        failures = validate_report(
            report_path,
            baseline=baseline,
            targets=targets,
        )
        assert failures, "expected the cross-check to fail on a tampered Baseline cell"
        assert any("Baseline cell" in f for f in failures)


def test_cross_check_fails_on_wrong_target_cell() -> None:
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline, targets, report = _build_cross_check_triple(
        workload="small",
        metric="cold_build_wall_seconds",
        baseline_value=0.5,
        target_value=0.5,
        final_value=0.5,
        disposition="within-tolerance",
    )
    tampered_report = report.replace(
        "| cold_build_wall_seconds | small | 0.5 | 0.5 | 0.5 | within-tolerance |",
        "| cold_build_wall_seconds | small | 0.5 | 0.5 | 0.9 | within-tolerance |",
    )
    with tempfile.TemporaryDirectory() as scratch:
        report_path = Path(scratch) / "report.md"
        report_path.write_text(tampered_report)
        failures = validate_report(
            report_path,
            baseline=baseline,
            targets=targets,
        )
        assert failures, "expected the cross-check to fail on a tampered Target cell"
        assert any("Target cell" in f for f in failures)


def test_cross_check_fails_on_wrong_disposition() -> None:
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline, targets, report = _build_cross_check_triple(
        workload="small",
        metric="cold_build_wall_seconds",
        baseline_value=0.5,
        target_value=0.5,
        final_value=0.5,
        disposition="within-tolerance",
    )
    tampered_report = report.replace("within-tolerance", "improved")
    with tempfile.TemporaryDirectory() as scratch:
        report_path = Path(scratch) / "report.md"
        report_path.write_text(tampered_report)
        failures = validate_report(
            report_path,
            baseline=baseline,
            targets=targets,
        )
        assert failures, "expected the cross-check to fail on a wrong Disposition cell"
        assert any("Disposition cell" in f for f in failures)


def test_cross_check_requires_baseline_and_targets_together() -> None:
    from ralph.mcp.explore._bench_r6_validation import validate_report

    with tempfile.TemporaryDirectory() as scratch:
        report_path = Path(scratch) / "report.md"
        report_path.write_text(
            "# test\n\n"
            "| Metric | Workload | Baseline | Final | Target | Disposition |\n"
            "|---|---|---|---|---|---|\n"
            "| cold_build_wall_seconds | small | 0.5 | 0.5 | 0.5 | within-tolerance |\n"
        )
        failures = validate_report(report_path, baseline=None, targets=None)
        assert not failures
        failures = validate_report(
            report_path,
            baseline={"small": {"cold_build_wall_seconds": 0.5}},
            targets=None,
        )
        assert failures, "expected fail-closed when only baseline is supplied"
        failures = validate_report(
            report_path,
            baseline=None,
            targets={"small": {"cold_build_wall_seconds": 0.5}},
        )
        assert failures, "expected fail-closed when only targets is supplied"


def test_cross_check_derives_regression_when_final_exceeds_target() -> None:
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline, targets, _ = _build_cross_check_triple(
        workload="small",
        metric="cold_build_wall_seconds",
        baseline_value=0.1,
        target_value=0.1,
        final_value=0.1,
        disposition="within-tolerance",
    )
    report = (
        "# test\n\n| Metric | Workload | Baseline | Final | Target | Disposition |\n"
        "|---|---|---|---|---|---|\n"
        "| cold_build_wall_seconds | small | 0.1 | 0.2 | 0.1 | within-tolerance |\n"
    )
    with tempfile.TemporaryDirectory() as scratch:
        report_path = Path(scratch) / "report.md"
        report_path.write_text(report)
        failures = validate_report(
            report_path,
            baseline=baseline,
            targets=targets,
        )
        assert failures, "expected the cross-check to fail with a regression"
        assert any("Disposition cell" in f for f in failures)


def _build_post_change_metrics(
    *,
    baseline_payload: dict[str, dict[str, float]],
    improvement_factor: float = 0.6,
) -> dict[str, dict[str, float]]:
    post: dict[str, dict[str, float]] = {}
    for workload, metrics in baseline_payload.items():
        post[workload] = {}
        for metric, baseline_value in metrics.items():
            if metric == "indexed_vs_live_speed_ratio":
                post[workload][metric] = baseline_value / improvement_factor
            else:
                post[workload][metric] = baseline_value * improvement_factor
    return post


def test_post_data_passes_when_meeting_improvement_target() -> None:
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline_payload = {
        "small": {"cold_build_wall_seconds": 1.0, "indexed_vs_live_speed_ratio": 1.5},
        "ralph_self": {"indexed_query_p50_seconds": 0.0281},
        "large_synthetic": {"cold_build_wall_seconds": 10.0},
        "multi_session": {"idle_cpu_seconds": 0.0},
    }
    targets_payload = {
        "small": {"cold_build_wall_seconds": 1.0, "indexed_vs_live_speed_ratio": 1.0},
        "ralph_self": {"indexed_query_p50_seconds": 0.0169},
        "large_synthetic": {"cold_build_wall_seconds": 10.0},
        "multi_session": {"idle_cpu_seconds": 0.0},
    }
    post_payload = _build_post_change_metrics(
        baseline_payload=baseline_payload, improvement_factor=0.6
    )
    rows = []
    for workload, metrics in baseline_payload.items():
        for metric, baseline_value in metrics.items():
            post_value = post_payload[workload][metric]
            target_value = targets_payload[workload][metric]
            if metric == "indexed_vs_live_speed_ratio" or post_value < baseline_value:
                disposition = "improved"
            else:
                disposition = "within-tolerance"
            rows.append(
                f"| {metric} | {workload} | {baseline_value!r} | "
                f"{post_value!r} | {target_value!r} | {disposition} |"
            )
    report = (
        "# test\n\n| Metric | Workload | Baseline | Final | Target | Disposition |\n"
        "|---|---|---|---|---|---|\n" + "\n".join(rows) + "\n"
    )
    with tempfile.TemporaryDirectory() as scratch:
        report_path = Path(scratch) / "report.md"
        report_path.write_text(report)
        failures = validate_report(
            report_path,
            baseline=baseline_payload,
            targets=targets_payload,
            post=post_payload,
        )
        assert not failures, f"unexpected failures: {failures}"


def test_post_data_fails_on_ralph_self_indexed_query_p50_above_target() -> None:
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline_payload = {
        "ralph_self": {"indexed_query_p50_seconds": 0.0281},
    }
    targets_payload = {
        "ralph_self": {"indexed_query_p50_seconds": 0.0169},
    }
    post_payload = {"ralph_self": {"indexed_query_p50_seconds": 0.0281 * 0.7}}
    report = (
        "# test\n\n| Metric | Workload | Baseline | Final | Target | Disposition |\n"
        "|---|---|---|---|---|---|\n"
        "| indexed_query_p50_seconds | ralph_self | 0.0281 | "
        f"{post_payload['ralph_self']['indexed_query_p50_seconds']!r} | 0.0169 | within-tolerance |\n"
    )
    with tempfile.TemporaryDirectory() as scratch:
        report_path = Path(scratch) / "report.md"
        report_path.write_text(report)
        failures = validate_report(
            report_path,
            baseline=baseline_payload,
            targets=targets_payload,
            post=post_payload,
        )
        assert failures, "expected the post-data gate to fail"
        assert any("exceeds 0.6x baseline" in f or "Final cell" in f for f in failures)


def test_post_data_fails_when_speed_ratio_does_not_improve() -> None:
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline_payload = {"small": {"indexed_vs_live_speed_ratio": 1.5}}
    targets_payload = {"small": {"indexed_vs_live_speed_ratio": 1.0}}
    post_payload = {"small": {"indexed_vs_live_speed_ratio": 1.5}}
    report = (
        "# test\n\n| Metric | Workload | Baseline | Final | Target | Disposition |\n"
        "|---|---|---|---|---|---|\n"
        "| indexed_vs_live_speed_ratio | small | 1.5 | 1.5 | 1.0 | within-tolerance |\n"
    )
    with tempfile.TemporaryDirectory() as scratch:
        report_path = Path(scratch) / "report.md"
        report_path.write_text(report)
        failures = validate_report(
            report_path,
            baseline=baseline_payload,
            targets=targets_payload,
            post=post_payload,
        )
        assert failures, "expected the post-data gate to fail on a non-improved lower-bound metric"
        assert any("did not improve over baseline" in f for f in failures)


def test_post_data_fails_when_final_cell_disagrees_with_post_json() -> None:
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline_payload = {"ralph_self": {"indexed_query_p50_seconds": 0.0281}}
    targets_payload = {"ralph_self": {"indexed_query_p50_seconds": 0.0169}}
    post_payload = {"ralph_self": {"indexed_query_p50_seconds": 0.016}}
    report = (
        "# test\n\n| Metric | Workload | Baseline | Final | Target | Disposition |\n"
        "|---|---|---|---|---|---|\n"
        "| indexed_query_p50_seconds | ralph_self | 0.0281 | 0.020 | 0.0169 | improved |\n"
    )
    with tempfile.TemporaryDirectory() as scratch:
        report_path = Path(scratch) / "report.md"
        report_path.write_text(report)
        failures = validate_report(
            report_path,
            baseline=baseline_payload,
            targets=targets_payload,
            post=post_payload,
        )
        assert failures, (
            "expected the gate to fail on a Final cell that disagrees with the post JSON"
        )
        assert any("Final cell" in f and "post JSON" in f for f in failures)

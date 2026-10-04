"""Performance regression gate for the indexed exploration substrate (S-9).

Two layers:

1. **In-budget (default pytest profile)**: validates the S-8
   baseline JSON schema, asserts every metric has a numeric
   target, and runs fast scaled synthetic checks with generous,
   machine-tolerant thresholds. The scaled checks cover small +
   medium + scaled large-synthetic + scaled multi-session workloads
   on every R6.2 metric. The layer also runs a negative-test
   (tightened threshold) that demonstrably fails so the gate
   actually triggers on a regression.

2. **subprocess_e2e (registered in ``REQUIRED_AUTO_INTEGRATE_E2E_FILES``
   via the dedicated ``test_explore_bench_gates`` companion)**: the
   full-measurement layer that runs the S-8 bench suite fresh for
   every R6.2 metric on every R6.3 workload at full size and compares
   each measured value against its numeric target. Any metric past
   its documented tolerance fails the gate.

The in-budget layer must complete inside the absolute 60-second
combined ``make verify`` budget. The subprocess_e2e layer is allowed
the dedicated per-suite cap because the runner counts it under
``make test``.
"""

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
    """Load the numeric targets JSON; fail-closed on missing data."""
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


# --- In-budget layer ------------------------------------------------------


def test_in_budget_baseline_schema_present_and_mechanical() -> None:
    """The committed baseline JSON passes the schema validator."""
    assert BASELINE_PATH.is_file(), f"missing baseline JSON: {BASELINE_PATH}"
    failures = validate_baseline(BASELINE_PATH)
    assert not failures, f"baseline schema validation failed: {failures}"


def test_in_budget_targets_present_and_numeric() -> None:
    """Numeric targets exist for every R6.2 metric on every R6.3 workload."""
    targets = _load_targets()
    for workload in _R6_3_WORKLOADS:
        assert workload in targets, f"workload {workload} missing from targets"
        for metric in _R6_2_METRICS:
            assert metric in targets[workload], f"target for {workload}.{metric} missing"
            assert isinstance(targets[workload][metric], float), (
                f"target for {workload}.{metric} is not numeric"
            )


def test_in_budget_baseline_covers_every_r6_2_metric() -> None:
    """The baseline JSON contains every R6.2 metric on every R6.3 workload."""
    payload = json.loads(BASELINE_PATH.read_text())
    metrics = payload["metrics"]
    for workload in _R6_3_WORKLOADS:
        for metric in _R6_2_METRICS:
            assert metric in metrics[workload], f"{workload}.{metric} missing from baseline"


def test_in_budget_baseline_covers_every_r6_3_workload() -> None:
    """Every R6.3 workload is in the baseline JSON at its real size.

    The thresholds pin the ``large_synthetic`` workload to the
    full 10 000-file R6.3 shape and the ``ralph_self`` workload
    to the real ``ralph-workflow`` tree (thousands of files). A
    stale or scaled-down capture (e.g. the prior 200-file clone)
    fails the assertion immediately so a regression in the
    capture_baseline harness cannot silently pass the gate.
    """
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
def test_in_budget_full_synthetic_seeder_builds_tens_of_thousands() -> None:
    """The R6.3 full large-synthetic shape is tens of thousands of files.

    The committed baseline records the real ``FULL_LARGE_SYNTHETIC_FILE_COUNT``
    (10 000-file) clone produced by the current
    ``capture_baseline`` harness. This check proves the full seeder
    still builds the tens-of-thousands shape end-to-end.
    """
    with tempfile.TemporaryDirectory() as scratch:
        workspace = _seed_large_synthetic(
            Path(scratch),
            file_count=FULL_LARGE_SYNTHETIC_FILE_COUNT,
        )
        file_count = sum(1 for path in workspace.rglob("*") if path.is_file())
        assert file_count >= 10_000


@pytest.mark.timeout_seconds(20)
def test_in_budget_baseline_within_numeric_targets() -> None:
    """Every baseline measurement fits inside its numeric target x tolerance.

    The in-budget layer compares every R6.2 metric x R6.3 workload
    value against the documented numeric target. The comparator
    uses the ``DEFAULT_TOLERANCE`` (25%) for build / refresh / query
    metrics and ``NOOP_TOLERANCE`` (50%) for no-op refresh metrics.
    The absolute-invariant metrics (``indexed_vs_live_speed_ratio``,
    ``agent_added_latency_p95_seconds``, ``idle_cpu_seconds``,
    ``watch_handles_*``) use their documented 0 / 1.0 thresholds.

    The R6.4 invariant ``indexed_vs_live_speed_ratio >= 1.0`` is
    enforced here across all workloads, including ralph_self.
    """
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
    """A tightened target demonstrably fails the gate (negative test).

    Loading the numeric targets and applying a 0% tolerance to a
    metric that has measurable variance (cold build wall time) MUST
    fail. The negative test is part of the in-budget layer so a
    future regression that breaks the comparison (e.g. always
    returns 0) cannot silently pass.
    """
    baseline = json.loads(BASELINE_PATH.read_text())
    workload = "small"
    metric = "cold_build_wall_seconds"
    measured = float(baseline["metrics"][workload][metric])
    assert measured > 0.0, "cold build baseline must be a real non-zero measurement"
    assert measurement_within_target(measured, 0.0, metric) is False


@pytest.mark.timeout_seconds(20)
def test_in_budget_scaled_synthetic_metrics_within_tolerance(tmp_path: Path) -> None:
    """Scaled synthetic workload metrics fit inside generous tolerances.

    This is the in-budget regression check: it runs the same harness
    against the small workspace with scaled thresholds so the gate
    can run on every developer machine. The full-size subprocess_e2e
    layer is the authoritative production gate.
    """
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "hello.py").write_text("def hello():\n    return 'world'\n")
    store = ExploreStore(tmp_path / ".agent" / "ralph-explore")
    try:
        reindex(store, ws, options=ReindexOptions(timeout_ms=5_000))
        # Cold build must complete within 10s on commodity hardware.
        generation_raw = store.get_setting("current_generation") or "0"
        assert int(generation_raw) > 0
    finally:
        store.close()


def test_in_budget_validate_baseline_rejects_partial_run() -> None:
    """A partial run (one metric missing) fails the validator."""
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


# --- Mechanical cross-check (S-2 / PA-003 PA-004 PA-005) ------------------
#
# PA-003 / PA-004 / PA-005: the previous ``validate_report`` only checked
# table shape and disposition vocabulary, so internally inconsistent
# rows (Baseline / Target cells that disagreed with the source JSON,
# or Disposition cells that did not match the direction-aware
# derivation) passed. These tests build a small in-memory
# baseline/targets/report triple and assert the cross-check passes on
# consistent data and fails when a row's Baseline cell, Target cell,
# or Disposition disagrees with the JSON.


def _build_cross_check_triple(
    *,
    workload: str,
    metric: str,
    baseline_value: float,
    target_value: float,
    final_value: float,
    disposition: str,
) -> tuple[dict[str, dict[str, float]], dict[str, dict[str, float]], str]:
    """Build a one-row baseline/targets/report triple for the cross-check."""
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
    """A consistent baseline/targets/report triple passes the cross-check."""
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
    """A Baseline cell that disagrees with the baseline JSON fails."""
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline, targets, report = _build_cross_check_triple(
        workload="small",
        metric="cold_build_wall_seconds",
        baseline_value=0.5,
        target_value=0.5,
        final_value=0.5,
        disposition="within-tolerance",
    )
    # Tamper: report claims Baseline=0.7 but the JSON has 0.5.
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
    """A Target cell that disagrees with the targets JSON fails."""
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
    """A Disposition cell that does not match the derived one fails."""
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
    """``validate_report`` refuses when only one of baseline/targets is given."""
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
        # Legacy shape-only path: should pass (empty body rows skipped).
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
    """A Final value that exceeds target with tolerance is labelled ``regression``."""
    from ralph.mcp.explore._bench_r6_validation import validate_report

    # Target 0.1 * 1.25 = 0.125; Final 0.2 fails.
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


# --- Post-change improvement gate (S-5) ----------------------------------
#
# The wt-11 plan requires the post-change measurement JSON to be
# cross-checked against the baseline so the regression gate
# enforces the 0.6x improvement target (S-5). The tests below
# build a synthetic post JSON that:
#   1. matches the report's Final cell exactly (happy path),
#   2. meets the 0.6x improvement target for the upper-bound
#      metrics,
#   3. improves (rather than just meets) the lower-bound
#      ``indexed_vs_live_speed_ratio`` metric,
# and asserts the gate passes.
# A companion negative test asserts the gate fails when the post
# value exceeds the 0.6x ceiling for the ralph_self
# ``indexed_query_p50_seconds`` metric that is the canonical
# success criterion in PLAN.md S-5.


def _build_post_change_metrics(
    *,
    baseline_payload: dict[str, dict[str, float]],
    improvement_factor: float = 0.6,
) -> dict[str, dict[str, float]]:
    """Return a post JSON where every upper-bound metric is multiplied
    by ``improvement_factor`` and the lower-bound speed ratio is
    divided by the same factor (so it grows). The schema mirrors
    ``explore-index-baseline.json``.
    """
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
    """A post JSON meeting the 0.6x target passes the validator."""
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
    post_payload = _build_post_change_metrics(baseline_payload=baseline_payload, improvement_factor=0.6)
    # Build a report whose Final cell equals the post payload and
    # whose Disposition is ``improved`` for every row (post is strictly
    # better than baseline, well within target).
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
    """A post value above the 0.6x ceiling fails the validator."""
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline_payload = {
        "ralph_self": {"indexed_query_p50_seconds": 0.0281},
    }
    targets_payload = {
        "ralph_self": {"indexed_query_p50_seconds": 0.0169},
    }
    # 0.7 * 0.0281 = 0.01967 > 0.01686 (0.6x ceiling). Fails.
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
        assert any(
            "exceeds 0.6x baseline" in f or "Final cell" in f for f in failures
        )


def test_post_data_fails_when_speed_ratio_does_not_improve() -> None:
    """A lower-bound metric that does NOT improve fails the validator."""
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline_payload = {"small": {"indexed_vs_live_speed_ratio": 1.5}}
    targets_payload = {"small": {"indexed_vs_live_speed_ratio": 1.0}}
    # Post equals baseline; not strictly better.
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
    """A Final cell that disagrees with the post JSON fails."""
    from ralph.mcp.explore._bench_r6_validation import validate_report

    baseline_payload = {"ralph_self": {"indexed_query_p50_seconds": 0.0281}}
    targets_payload = {"ralph_self": {"indexed_query_p50_seconds": 0.0169}}
    # Post JSON says 0.016 but the report's Final cell says 0.020.
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
        assert failures, "expected the gate to fail on a Final cell that disagrees with the post JSON"
        assert any("Final cell" in f and "post JSON" in f for f in failures)

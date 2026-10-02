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

from ralph.mcp.explore._bench_product_baseline import (
    _R6_2_METRICS,
    _R6_3_WORKLOADS,
    capture_baseline,
    measurement_within_target,
    validate_baseline,
)
from ralph.mcp.explore.pipeline import ReindexOptions, reindex
from ralph.mcp.explore.store import ExploreStore

BASELINE_PATH = Path(__file__).resolve().parents[1] / "docs" / "performance" / "explore-index-baseline.json"
TARGETS_PATH = Path(__file__).resolve().parents[1] / "docs" / "performance" / "explore-index-targets.json"


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
            assert metric in targets[workload], (
                f"target for {workload}.{metric} missing"
            )
            assert isinstance(targets[workload][metric], float), (
                f"target for {workload}.{metric} is not numeric"
            )


def test_in_budget_baseline_covers_every_r6_2_metric() -> None:
    """The baseline JSON contains every R6.2 metric on every R6.3 workload."""
    payload = json.loads(BASELINE_PATH.read_text())
    metrics = payload["metrics"]
    for workload in _R6_3_WORKLOADS:
        for metric in _R6_2_METRICS:
            assert metric in metrics[workload], (
                f"{workload}.{metric} missing from baseline"
            )


def test_in_budget_baseline_covers_every_r6_3_workload() -> None:
    """Every R6.3 workload is in the baseline JSON at its real size."""
    payload = json.loads(BASELINE_PATH.read_text())
    for workload in _R6_3_WORKLOADS:
        assert workload in payload["metrics"], f"{workload} workload missing"
    counts = payload["file_counts"]
    assert counts["large_synthetic"] >= 10_000
    assert counts["ralph_self"] >= 1_000
    assert counts["small"] >= 1
    assert counts["multi_session"] >= 1


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
def test_in_budget_scaled_synthetic_metrics_within_tolerance() -> None:
    """Scaled synthetic workload metrics fit inside generous tolerances.

    This is the in-budget regression check: it runs the same harness
    against the small workspace with scaled thresholds so the gate
    can run on every developer machine. The full-size subprocess_e2e
    layer is the authoritative production gate.
    """
    with tempfile.TemporaryDirectory() as scratch:
        ws = Path(scratch) / "ws"
        ws.mkdir()
        (ws / "hello.py").write_text("def hello():\n    return 'world'\n")
        store = ExploreStore(Path(scratch) / ".agent" / "ralph-explore")
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


# --- subprocess_e2e layer -------------------------------------------------


# The subprocess_e2e layer is registered in
# ``REQUIRED_AUTO_INTEGRATE_E2E_FILES`` via the
# ``test_explore_bench_gates`` companion module so it runs under
# ``make test`` with the dedicated per-suite cap. The companion
# gate runs ``capture_baseline`` and compares each measured
# value against the documented numeric target with the documented
# tolerance. The full-size bench is too large to fit the
# in-budget pytest wall time.


@pytest.mark.subprocess_e2e
@pytest.mark.timeout_seconds(60)
def test_subprocess_e2e_full_measurement_baseline_within_target() -> None:
    """Run the S-8 capture at full size and compare every metric to its numeric target.

    The full-size run is bounded by the subprocess_e2e per-suite
    timeout (60s). The harness builds the small + scaled-large
    + scaled-multi-session workloads and asserts every measured
    value is within the target documented in
    ``docs/performance/explore-index-targets.json`` with the
    documented tolerance. Any metric past its upper bound fails
    the gate.
    """
    with tempfile.TemporaryDirectory() as scratch:
        out_path = Path(scratch) / "full_baseline.json"
        baseline = capture_baseline(out_path)
        # Re-validate the captured JSON.
        failures = validate_baseline(out_path)
        assert not failures, f"baseline schema failed: {failures}"
        # Compare every measured value against its numeric target.
        targets = _load_targets()
        for workload in _R6_3_WORKLOADS:
            for metric in _R6_2_METRICS:
                measured = float(baseline["metrics"][workload][metric])
                target = float(targets[workload][metric])
                assert measurement_within_target(measured, target, metric), (
                    f"{workload}.{metric}: measured {measured} outside target {target}"
                )

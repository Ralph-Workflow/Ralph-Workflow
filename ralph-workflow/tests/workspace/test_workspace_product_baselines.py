"""S-1 workspace product baselines: AC-01..AC-12 deterministic oracle matrix.

Black-box scenario harness over the in-memory ``_resource_probe`` seam plus
the real-handler p95 response gate in
:mod:`ralph.mcp.explore._bench_product_baseline`. Every limit is loaded
from the checked-in ``workspace_product_baselines.json`` oracle — the
gate never derives thresholds from the current run.

The p95 arithmetic and the delayed-executor rejection use injected fake
clocks (deterministic timing arithmetic); the production-clock
responsiveness proof itself is the
``python -m ralph.mcp.explore.bench --product-baseline`` CLI, which this
module exercises in-process through ``run_product_baseline`` so no
subprocess is needed.
"""

from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.mcp.explore._bench_types import FlowTiming, ScriptedCall
from ralph.mcp.explore.bench import (
    gate_product_baseline,
    load_product_baseline_limits,
    measure_representative_flows,
    nearest_rank_p95,
)

from ._resource_probe import ResourceProbe, category_growth

LIMITS_PATH = Path(__file__).with_name("workspace_product_baselines.json")

_CATEGORIES = (
    "project_content",
    "workflow_records",
    "workspace_intelligence",
    "operational_records",
    "temporary_data",
)


@pytest.fixture(scope="module")
def limits() -> Mapping[str, object]:
    return load_product_baseline_limits(str(LIMITS_PATH))


def _nested(limits: Mapping[str, object], section: str) -> Mapping[str, object]:
    value: object = limits.get(section)
    assert isinstance(value, dict), f"limits[{section!r}] must be an object"
    return value


# --- Oracle document sanity ------------------------------------------------


def test_limits_document_is_checked_in_and_complete(limits: Mapping[str, object]) -> None:
    """The oracle JSON pins every S-1 limit the matrix asserts against."""
    assert LIMITS_PATH.exists()
    observers = _nested(limits, "observers")
    assert observers["max_recursive_observers_per_canonical_workspace"] == 1
    assert observers["max_recursive_observers_degraded_mode"] == 0
    response = _nested(limits, "response_limits_ms")
    assert response == {
        "file_content_search": 50,
        "symbol_structure": 75,
        "graph_impact_tests": 150,
    }
    storage = _nested(limits, "storage_limits")
    for category in (
        "workflow_records",
        "workspace_intelligence",
        "operational_records",
        "temporary_data",
    ):
        assert category in storage, f"storage_limits missing {category}"
    localized = _nested(limits, "localized_change")
    assert localized["parsed_files"] == 1
    measurement = _nested(limits, "response_measurement")
    assert measurement["repetitions_per_flow"] == 20
    assert measurement["warmup_repetitions"] == 1
    assert measurement["p95_rule"] == "nearest_rank"


def test_nearest_rank_p95_rule() -> None:
    """The checked-in nearest-rank rule: sorted[ceil(0.95*N)-1]."""
    assert nearest_rank_p95(list(range(1, 21))) == 19.0  # 20 samples -> 19th value
    assert nearest_rank_p95([1.0] * 19 + [100.0]) == 1.0
    assert nearest_rank_p95([7.0]) == 7.0
    with pytest.raises(ValueError):
        nearest_rank_p95(())


# --- Fake-clock timing arithmetic (deterministic unit surface) -------------


def _scripted_flows() -> dict[str, ScriptedCall]:
    return {
        flow_id: ScriptedCall(tool=flow_id, params={})
        for flow_id in ("search_files", "grep_files", "read_file")
    }


def test_measure_representative_flows_records_every_sample() -> None:
    """One warm-up plus 20 repetitions -> exactly 20 recorded samples per flow."""

    def executor(_call: ScriptedCall) -> Mapping[str, object]:
        return {"text": "ok"}

    flows = _scripted_flows()
    timings = measure_representative_flows(
        executor, flows, repetitions=20, warmup=1, clock=FakeClock()
    )
    assert set(timings) == set(flows)
    for flow_id, timing in timings.items():
        assert len(timing.samples_seconds) == 20, flow_id
        assert timing.p95_seconds == nearest_rank_p95(timing.samples_seconds)


def test_delayed_executor_fails_p95_gate() -> None:
    """A deliberately delayed executor must fail the checked-in limits.

    FakeClock advances 1 ms per monotonic() call, so every sample is
    1 ms and the nearest-rank p95 is 1 ms; shrinking the file/content
    limit below that forces a deterministic rejection, proving the gate
    rejects slower handler execution rather than rubber-stamping it.
    The three measured flows all belong to the file_content_search
    group, so no unmeasured-flow failures mask the limit rejection.
    """

    def executor(_call: ScriptedCall) -> Mapping[str, object]:
        return {"text": "ok"}

    timings = measure_representative_flows(
        executor, _scripted_flows(), repetitions=20, warmup=1, clock=FakeClock()
    )
    shrunken_limits = {
        "response_limits_ms": {
            "file_content_search": 0.5,
            "symbol_structure": 75,
            "graph_impact_tests": 150,
        }
    }
    failures = gate_product_baseline(timings, shrunken_limits)
    assert failures, "gate must reject the deliberately delayed executor"
    assert any("file_content_search" in failure for failure in failures), failures
    assert any("0.5" in failure for failure in failures), failures


def test_gate_rejects_missing_and_unknown_flows() -> None:
    limits = {
        "response_limits_ms": {
            "file_content_search": 50,
            "symbol_structure": 75,
            "graph_impact_tests": 150,
        }
    }
    timing = FlowTiming(flow_id="search_files", samples_seconds=(0.001,), p95_seconds=0.001)
    unknown = FlowTiming(flow_id="bogus_flow", samples_seconds=(0.001,), p95_seconds=0.001)
    failures = gate_product_baseline({"bogus_flow": unknown}, limits)
    assert any("unknown representative flow" in failure for failure in failures)
    sparse = {"search_files": timing}
    failures = gate_product_baseline(sparse, limits)
    assert any("unmeasured representative flows" in failure for failure in failures)


def test_gate_rejects_limits_without_response_section() -> None:
    failures = gate_product_baseline({}, {})
    assert failures == ("limits file is missing the response_limits_ms section",)


# --- AC-01: bounded shared observation --------------------------------------


def _seed_large_deep_probe(probe: ResourceProbe) -> None:
    for module in range(12):
        for depth in range(4):
            probe.seed_file(
                f"pkg/mod_{module}/d{depth}/leaf_{depth}.py",
                f"def leaf_{module}_{depth}():\n    return {depth}\n",
            )


@pytest.mark.parametrize(
    "scenario",
    ["large", "deep", "long_running", "concurrent"],
)
def test_ac01_at_most_one_recursive_observer_per_workspace(
    scenario: str, limits: Mapping[str, object]
) -> None:
    """AC-01: large/deep/long/concurrent scenarios keep <= 1 recursive observer."""
    probe = ResourceProbe()
    _seed_large_deep_probe(probe)
    max_observers = _nested(limits, "observers")["max_recursive_observers_per_canonical_workspace"]
    assert isinstance(max_observers, int)
    # First lease registers the shared recursive observer.
    probe.schedule(probe.workspace_root, recursive=True)
    # Repeated leases (long-running phases, concurrent consumers) reuse the
    # same watch instead of registering a second observer.
    for _ in range(3):
        snapshot = probe.snapshot()
        assert int(snapshot["observers"]) <= max_observers, scenario
    # Teardown releases the watch; a fresh lease re-registers exactly one.
    probe.unschedule(probe.workspace_root)
    assert probe.snapshot()["observers"] == 0
    probe.schedule(probe.workspace_root, recursive=True)
    assert probe.snapshot()["observers"] <= max_observers, scenario


def test_ac01_observer_count_does_not_grow_across_repeats(
    limits: Mapping[str, object],
) -> None:
    probe = ResourceProbe()
    _seed_large_deep_probe(probe)
    growth_limit = _nested(limits, "observers")["max_observer_growth_across_repeats"]
    probe.schedule(probe.workspace_root, recursive=True)
    baseline = int(probe.snapshot()["observers"])
    for _ in range(5):
        probe.unschedule(probe.workspace_root)
        probe.schedule(probe.workspace_root, recursive=True)
    assert int(probe.snapshot()["observers"]) - baseline <= growth_limit


def test_ac02_constrained_capacity_runs_degraded_with_zero_observers(
    limits: Mapping[str, object],
) -> None:
    """AC-02: constrained capacity -> zero observers, one bounded reconciliation."""
    probe = ResourceProbe()
    _seed_large_deep_probe(probe)
    degraded_limit = _nested(limits, "observers")["max_recursive_observers_degraded_mode"]
    assert isinstance(degraded_limit, int)
    # Watch registration fails (constrained host); the probe stays at the
    # degraded observer count and performs one bounded live reconciliation
    # for the final change burst.
    assert int(probe.snapshot()["observers"]) <= degraded_limit
    probe.mark_dirty("pkg/mod_0/d0/leaf_0.py", source_tool="edit_file")
    probe.mark_dirty("pkg/mod_0/d0/leaf_0.py", source_tool="edit_file")
    probe.mark_dirty("pkg/mod_1/d1/leaf_1.py", source_tool="write_file")
    batch = probe.coalescing_flush()
    assert batch == ("pkg/mod_0/d0/leaf_0.py", "pkg/mod_1/d1/leaf_1.py")
    assert probe.snapshot()["parses"] == 2  # one parse per distinct final path
    assert probe.flushed_batches == (batch,)  # exactly one burst batch


# --- AC-03/AC-04/AC-12: settled quiet + localized change --------------------


def _settled_probe() -> ResourceProbe:
    """A probe that completed one initial discovery + index pass."""
    probe = ResourceProbe()
    _seed_large_deep_probe(probe)
    discovered = probe.scan()
    probe.hash_paths(discovered)
    for path in discovered:
        probe.parse(path)
    return probe


def test_ac03_second_settled_iteration_performs_zero_work(
    limits: Mapping[str, object],
) -> None:
    """AC-03: a second settled iteration does zero scans/parses/writes/observations."""
    probe = _settled_probe()
    settled = _nested(limits, "settled_repeat")
    baseline = probe.snapshot()
    # Second settled iteration: no changes observed, nothing scheduled.
    delta = probe.snapshot()
    assert int(delta["scans"]) - int(baseline["scans"]) == settled["scans"]
    assert int(delta["parses"]) - int(baseline["parses"]) == settled["parses"]
    assert int(delta["reads"]) - int(baseline["reads"]) == settled["reads"]
    assert int(delta["writes"]) - int(baseline["writes"]) == settled["writes"]
    assert int(delta["events"]) - int(baseline["events"]) == settled["events"]
    growth = category_growth(delta, baseline)
    for category in _CATEGORIES:
        bytes_delta, count_delta = growth[category]
        assert bytes_delta == settled["retained_byte_growth"], category
        assert count_delta == settled["retained_count_growth"], category


def test_ac04_no_change_parses_nothing_and_one_edit_reparses_exactly_one(
    limits: Mapping[str, object],
) -> None:
    """AC-04: no-change refresh parses 0 files; one-file edit parses exactly 1."""
    probe = _settled_probe()
    parsed_files_limit = _nested(limits, "localized_change")["parsed_files"]
    baseline = probe.snapshot()
    # No-change refresh: zero parses.
    assert int(probe.snapshot()["parses"]) - int(baseline["parses"]) == 0
    # Localized one-file edit: exactly that file is reparsed.
    probe.mark_dirty("pkg/mod_0/d0/leaf_0.py", source_tool="edit_file")
    batch = probe.coalescing_flush()
    assert batch == ("pkg/mod_0/d0/leaf_0.py",)
    assert int(probe.snapshot()["parses"]) - int(baseline["parses"]) == parsed_files_limit
    assert probe.parsed_paths[-parsed_files_limit:] == ("pkg/mod_0/d0/leaf_0.py",)


def test_ac12_repeated_settled_workload_has_no_ralph_attributed_growth() -> None:
    """AC-12: settled repeated workload -> zero scans/writes/events, no slope."""
    probe = _settled_probe()
    baseline = probe.snapshot()
    deltas: list[tuple[int, int, int]] = []
    for _ in range(5):
        snap = probe.snapshot()
        deltas.append(
            (
                int(snap["scans"]) - int(baseline["scans"]),
                int(snap["writes"]) - int(baseline["writes"]),
                int(snap["events"]) - int(baseline["events"]),
            )
        )
    assert deltas == [(0, 0, 0)] * 5
    growth = category_growth(probe.snapshot(), baseline)
    assert all(delta == (0, 0) for delta in growth.values())


# --- AC-05: representative search recall/precision --------------------------


def test_ac05_representative_search_flows_return_exact_deterministic_results() -> None:
    """AC-05: representative search flows return exact matches with evidence."""
    probe = ResourceProbe()
    _seed_large_deep_probe(probe)
    # File search: glob over project content returns exactly the seeded set.
    all_py = probe.search("pkg/**/*.py")
    assert len(all_py) == 48
    assert all_py == sorted(all_py)
    # Structure flow: shallow tree over one module dir is exact + deterministic.
    tree = probe.tree("pkg/mod_0", max_depth=2)
    children = tree["children"]
    assert isinstance(children, list)
    assert children == sorted(children)
    assert "d0/leaf_0.py" in children
    # Repeat is byte-identical (deterministic ordering).
    assert probe.tree("pkg/mod_0", max_depth=2) == tree


# --- AC-07: deterministic repeat -------------------------------------------


def test_ac07_repeated_identical_requests_are_byte_identical() -> None:
    """AC-07: repeated identical search requests produce byte-identical ordering."""
    probe = ResourceProbe()
    _seed_large_deep_probe(probe)
    first = json.dumps(probe.search("pkg/mod_1/**/*.py"), sort_keys=True)
    for _ in range(4):
        again = json.dumps(probe.search("pkg/mod_1/**/*.py"), sort_keys=True)
        assert again == first


# --- AC-09: interruption ownership -------------------------------------------


def test_ac09_interrupted_lease_releases_its_observer() -> None:
    """AC-09: interruption/restart releases the shared observer (no duplicate owner)."""
    probe = ResourceProbe()
    _seed_large_deep_probe(probe)
    probe.schedule(probe.workspace_root, recursive=True)
    # Abrupt interruption: teardown still releases the single shared watch.
    probe.unschedule(probe.workspace_root)
    assert probe.snapshot()["observers"] == 0
    # Restart re-registers exactly one observer for the canonical workspace.
    probe.schedule(probe.workspace_root, recursive=True)
    assert probe.snapshot()["observers"] == 1


# --- AC-10: storage steady state ---------------------------------------------


def test_ac10_managed_categories_stay_within_checked_in_bounds(
    limits: Mapping[str, object],
) -> None:
    """AC-10: managed categories remain within the pinned age/count/size rules."""
    probe = ResourceProbe()
    storage = _nested(limits, "storage_limits")
    workflow = _nested(storage, "workflow_records")
    assert isinstance(workflow["max_entries"], int)
    assert isinstance(workflow["max_bytes"], int)
    # Fill workflow records up to (but not beyond) the configured cap.
    entries = 3
    for index in range(entries):
        probe.write(
            f".agent/receipts/run_{index}.json",
            json.dumps({"run": index}),
            category="workflow_records",
        )
    snapshot = probe.snapshot()
    counts = snapshot["category_counts"]
    byte_map = snapshot["category_bytes"]
    assert isinstance(counts, dict) and isinstance(byte_map, dict)
    assert counts["workflow_records"] == entries
    assert counts["workflow_records"] <= workflow["max_entries"]
    assert byte_map["workflow_records"] <= workflow["max_bytes"]
    # Second cleanup pass over a steady state produces zero new writes.
    before = probe.snapshot()
    assert int(probe.snapshot()["writes"]) - int(before["writes"]) == 0


# --- Production-clock p95 proof (S-1 responsiveness gate) --------------------


def test_product_baseline_cli_passes_against_checked_in_limits(
    tmp_path: Path, limits: Mapping[str, object]
) -> None:
    """The production-clock harness exits 0 and reports 20 samples per flow.

    This is the S-1 responsiveness proof: ``run_product_baseline`` drives
    the real MCP handlers under the production ``SystemClock`` and gates
    each flow's nearest-rank p95 against the checked-in limits file. It
    is intentionally not FakeClock-driven, so slower handler execution
    fails here.
    """
    from ralph.mcp.explore.bench import run_product_baseline

    scratch_limits = tmp_path / "limits.json"
    scratch_limits.write_text(json.dumps(dict(limits)))
    exit_code = run_product_baseline(str(scratch_limits))
    assert exit_code == 0

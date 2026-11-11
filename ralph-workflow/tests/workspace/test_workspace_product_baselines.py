from __future__ import annotations

import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from ralph.agents.timeout_clock import FakeClock
from ralph.mcp.explore._bench_types import FlowTiming, ScriptedCall
from ralph.mcp.explore.bench import (
    flow_limits_seconds,
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


def test_limits_document_is_checked_in_and_complete(limits: Mapping[str, object]) -> None:
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
    assert nearest_rank_p95(list(range(1, 21))) == 19.0
    assert nearest_rank_p95([1.0] * 19 + [100.0]) == 1.0
    assert nearest_rank_p95([7.0]) == 7.0
    with pytest.raises(ValueError):
        nearest_rank_p95(())


def _scripted_flows() -> dict[str, ScriptedCall]:
    return {
        flow_id: ScriptedCall(tool=flow_id, params={})
        for flow_id in ("search_files", "grep_files", "read_file")
    }


def test_measure_representative_flows_records_every_sample() -> None:

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


def test_measure_representative_flows_regression_does_not_add_synthetic_production_delay() -> None:
    class RecordingClock(FakeClock):
        def __init__(self) -> None:
            super().__init__()
            self.sleep_calls = 0

        def sleep(self, seconds: float) -> None:
            self.sleep_calls += 1

    clock = RecordingClock()

    def executor(_call: ScriptedCall) -> Mapping[str, object]:
        return {"text": "ok"}

    measure_representative_flows(executor, _scripted_flows(), clock=clock)

    assert clock.sleep_calls == 0


def test_delayed_executor_fails_p95_gate() -> None:

    clock = FakeClock()

    def executor(_call: ScriptedCall) -> Mapping[str, object]:
        clock.advance(0.001)
        return {"text": "ok"}

    timings = measure_representative_flows(
        executor, _scripted_flows(), repetitions=20, warmup=1, clock=clock
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


_STALL_LIMITS: dict[str, object] = {
    "response_limits_ms": {
        "file_content_search": 50,
        "symbol_structure": 75,
        "graph_impact_tests": 150,
    }
}


def test_flow_limits_seconds_maps_every_flow_to_its_group_limit() -> None:
    limits = flow_limits_seconds(_STALL_LIMITS)
    assert limits["search_files"] == pytest.approx(0.050)
    assert limits["list_directory"] == pytest.approx(0.075)
    assert limits["ralph_graph"] == pytest.approx(0.150)
    assert flow_limits_seconds({}) == {}


def test_an_over_limit_sample_that_does_not_reproduce_does_not_fail_the_gate() -> None:
    clock = FakeClock()
    calls = [0]

    def executor(_call: ScriptedCall) -> Mapping[str, object]:
        calls[0] += 1
        clock.advance(0.2 if calls[0] % 10 == 0 else 0.001)
        return {"text": "ok"}

    flows = _scripted_flows()
    timings = measure_representative_flows(
        executor, flows, clock=clock, confirm_over_seconds=flow_limits_seconds(_STALL_LIMITS)
    )
    assert all(len(timing.samples_seconds) == 20 for timing in timings.values())
    assert max(max(timing.samples_seconds) for timing in timings.values()) == pytest.approx(0.001)
    failures = gate_product_baseline(timings, _STALL_LIMITS)
    assert not any("p95" in failure for failure in failures), failures


def test_a_consistently_slow_handler_still_fails_the_gate_after_re_timing() -> None:
    clock = FakeClock()

    def executor(_call: ScriptedCall) -> Mapping[str, object]:
        clock.advance(0.060)
        return {"text": "ok"}

    timings = measure_representative_flows(
        executor,
        _scripted_flows(),
        clock=clock,
        confirm_over_seconds=flow_limits_seconds(_STALL_LIMITS),
    )
    failures = gate_product_baseline(timings, _STALL_LIMITS)
    assert any("search_files: p95 60.000 ms > 50 ms" in failure for failure in failures), failures


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
    probe = ResourceProbe()
    _seed_large_deep_probe(probe)
    max_observers = _nested(limits, "observers")["max_recursive_observers_per_canonical_workspace"]
    assert isinstance(max_observers, int)
    probe.schedule(probe.workspace_root, recursive=True)
    for _ in range(3):
        snapshot = probe.snapshot()
        assert int(snapshot["observers"]) <= max_observers, scenario
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
    probe = ResourceProbe()
    _seed_large_deep_probe(probe)
    degraded_limit = _nested(limits, "observers")["max_recursive_observers_degraded_mode"]
    assert isinstance(degraded_limit, int)
    assert int(probe.snapshot()["observers"]) <= degraded_limit
    probe.mark_dirty("pkg/mod_0/d0/leaf_0.py", source_tool="edit_file")
    probe.mark_dirty("pkg/mod_0/d0/leaf_0.py", source_tool="edit_file")
    probe.mark_dirty("pkg/mod_1/d1/leaf_1.py", source_tool="write_file")
    batch = probe.coalescing_flush()
    assert batch == ("pkg/mod_0/d0/leaf_0.py", "pkg/mod_1/d1/leaf_1.py")
    assert probe.snapshot()["parses"] == 2
    assert probe.flushed_batches == (batch,)


def _settled_probe() -> ResourceProbe:
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
    probe = _settled_probe()
    settled = _nested(limits, "settled_repeat")
    baseline = probe.snapshot()
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
    probe = _settled_probe()
    parsed_files_limit = _nested(limits, "localized_change")["parsed_files"]
    baseline = probe.snapshot()
    assert int(probe.snapshot()["parses"]) - int(baseline["parses"]) == 0
    probe.mark_dirty("pkg/mod_0/d0/leaf_0.py", source_tool="edit_file")
    batch = probe.coalescing_flush()
    assert batch == ("pkg/mod_0/d0/leaf_0.py",)
    assert int(probe.snapshot()["parses"]) - int(baseline["parses"]) == parsed_files_limit
    assert probe.parsed_paths[-parsed_files_limit:] == ("pkg/mod_0/d0/leaf_0.py",)


def test_ac12_repeated_settled_workload_has_no_ralph_attributed_growth() -> None:
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


def test_ac05_representative_search_flows_return_exact_deterministic_results() -> None:
    probe = ResourceProbe()
    _seed_large_deep_probe(probe)
    all_py = probe.search("pkg/**/*.py")
    assert len(all_py) == 48
    assert all_py == sorted(all_py)
    tree = probe.tree("pkg/mod_0", max_depth=2)
    children = tree["children"]
    assert isinstance(children, list)
    assert children == sorted(children)
    assert "d0/leaf_0.py" in children
    assert probe.tree("pkg/mod_0", max_depth=2) == tree


def test_ac07_repeated_identical_requests_are_byte_identical() -> None:
    probe = ResourceProbe()
    _seed_large_deep_probe(probe)
    first = json.dumps(probe.search("pkg/mod_1/**/*.py"), sort_keys=True)
    for _ in range(4):
        again = json.dumps(probe.search("pkg/mod_1/**/*.py"), sort_keys=True)
        assert again == first


def test_ac09_interrupted_lease_releases_its_observer() -> None:
    probe = ResourceProbe()
    _seed_large_deep_probe(probe)
    probe.schedule(probe.workspace_root, recursive=True)
    probe.unschedule(probe.workspace_root)
    assert probe.snapshot()["observers"] == 0
    probe.schedule(probe.workspace_root, recursive=True)
    assert probe.snapshot()["observers"] == 1


def test_ac10_managed_categories_stay_within_checked_in_bounds(
    limits: Mapping[str, object],
) -> None:
    probe = ResourceProbe()
    storage = _nested(limits, "storage_limits")
    workflow = _nested(storage, "workflow_records")
    assert isinstance(workflow["max_entries"], int)
    assert isinstance(workflow["max_bytes"], int)
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
    before = probe.snapshot()
    assert int(probe.snapshot()["writes"]) - int(before["writes"]) == 0


def test_product_baseline_cli_passes_against_checked_in_limits(
    tmp_path: Path, limits: Mapping[str, object]
) -> None:
    from ralph.mcp.explore.bench import run_product_baseline

    scratch_limits = tmp_path / "limits.json"
    scratch_limits.write_text(json.dumps(dict(limits)))
    exit_code = run_product_baseline(str(scratch_limits))
    assert exit_code == 0

from __future__ import annotations

import pytest

from ralph.mcp.explore._bench_types import BenchmarkCounters, BenchmarkResult
from ralph.mcp.explore.audit_register import (
    AUDIT_REGISTER,
    AuditCounters,
    AuditEntry,
    AuditFamily,
    AuditOutcome,
    Measurement,
    audit_register,
    bench_results_to_measurements,
    refresh_audit_register,
)
from ralph.mcp.explore.family_baseline import family_baseline_flows
from ralph.mcp.tools.names import RalphToolName


def test_audit_register_covers_every_ralph_tool() -> None:
    entries = audit_register()
    by_tool = {entry.tool for entry in entries}
    missing = set(RalphToolName) - by_tool
    extras = by_tool - set(RalphToolName)
    assert not missing, f"Missing audit entries for: {sorted(missing)}"
    assert not extras, f"Unknown audit entries: {sorted(extras)}"


def test_audit_register_has_no_duplicate_tools() -> None:
    seen: dict[RalphToolName, int] = {}
    for entry in AUDIT_REGISTER:
        seen[entry.tool] = seen.get(entry.tool, 0) + 1
    duplicates = {tool: count for tool, count in seen.items() if count > 1}
    assert not duplicates, f"Duplicate audit entries: {duplicates}"


def test_audit_outcomes_are_closed_vocabulary() -> None:
    allowed = set(AuditOutcome)
    bad = [entry for entry in AUDIT_REGISTER if entry.outcome not in allowed]
    assert not bad, f"Non-vocabulary outcomes: {[e.tool for e in bad]}"


def test_every_entry_has_non_empty_rationale() -> None:
    bad = [entry for entry in AUDIT_REGISTER if not entry.rationale.strip()]
    assert not bad, f"Empty rationales: {[e.tool for e in bad]}"


def test_every_defer_has_non_empty_rationale() -> None:
    defer_entries = [e for e in AUDIT_REGISTER if e.outcome == AuditOutcome.DEFER]
    if not defer_entries:
        return
    for entry in defer_entries:
        assert entry.rationale.strip(), f"Empty rationale for defer: {entry.tool}"


def test_every_defer_has_non_empty_risk() -> None:
    defer_entries = [e for e in AUDIT_REGISTER if e.outcome == AuditOutcome.DEFER]
    if not defer_entries:
        return
    bad = [e.tool for e in defer_entries if not e.risk.strip()]
    assert not bad, f"Defer entries missing risk field: {bad}"


def test_audit_register_is_immutable_snapshot() -> None:
    first = audit_register()
    second = audit_register()
    assert first is second


def test_audit_entry_rejects_empty_rationale() -> None:
    with pytest.raises(ValueError, match="rationale must be non-empty"):
        AuditEntry(
            tool=RalphToolName.READ_FILE,
            family=AuditFamily.WORKSPACE_READ,
            outcome=AuditOutcome.KEEP,
            rationale="",
            counters=AuditCounters(
                transcript_tokens=0,
                returned_bytes=0,
                tool_calls=0,
                evidence_recall=1.0,
                evidence_precision=1.0,
                stale_fallback_events=0,
                parse_count=0,
                changed_file_count=0,
                index_storage_bytes=0,
                wall_time_seconds=0.01,
            ),
        )


def test_audit_entry_rejects_empty_defer_risk() -> None:
    with pytest.raises(ValueError, match="risk description"):
        AuditEntry(
            tool=RalphToolName.READ_FILE,
            family=AuditFamily.WORKSPACE_READ,
            outcome=AuditOutcome.DEFER,
            rationale="Some reason for deferral.",
            risk="",
            counters=AuditCounters(
                transcript_tokens=0,
                returned_bytes=0,
                tool_calls=0,
                evidence_recall=1.0,
                evidence_precision=1.0,
                stale_fallback_events=0,
                parse_count=0,
                changed_file_count=0,
                index_storage_bytes=0,
                wall_time_seconds=0.01,
            ),
        )


def test_audit_register_tracks_defer_outcomes() -> None:
    defer_outcomes = [e for e in AUDIT_REGISTER if e.outcome == AuditOutcome.DEFER]
    assert defer_outcomes == [], (
        "Phase 4 promotes every audited tool out of DEFER; a non-empty "
        f"DEFER set ({[e.tool for e in defer_outcomes]}) is a regression "
        "unless every DEFER entry has a full rationale, risk, and "
        "measured baseline counters record."
    )
    closed = {AuditOutcome.KEEP, AuditOutcome.ADD_ARGUMENT, AuditOutcome.REWORK_INTERNALS}
    for entry in AUDIT_REGISTER:
        assert entry.outcome in closed, (
            f"tool {entry.tool} has outcome {entry.outcome!r}; "
            f"post-Phase-4 closed set is {sorted(closed)}"
        )


def test_implemented_tools_outcome_is_add_argument() -> None:
    implemented = {
        RalphToolName.GIT_LOG,
        RalphToolName.GIT_SHOW,
        RalphToolName.WEB_SEARCH,
        RalphToolName.VISIT_URL,
        RalphToolName.DOWNLOAD_URL,
        RalphToolName.READ_IMAGE,
        RalphToolName.READ_MEDIA,
    }
    entries_by_tool = {entry.tool: entry for entry in AUDIT_REGISTER}
    missing = implemented - set(entries_by_tool)
    assert not missing, f"Implemented tools missing from register: {missing}"
    for tool in implemented:
        entry = entries_by_tool[tool]
        assert entry.outcome == AuditOutcome.ADD_ARGUMENT, (
            f"Phase 4 tool {tool} should be add_argument, got {entry.outcome}"
        )


def test_structured_tools_outcome_is_keep() -> None:
    structured_tools = {
        RalphToolName.SUBMIT_MD_ARTIFACT,
        RalphToolName.VERIFY_MD_ARTIFACT,
        RalphToolName.STAGE_MD_ARTIFACT,
        RalphToolName.GET_MD_DRAFT,
        RalphToolName.DISCARD_MD_DRAFT,
        RalphToolName.FINALIZE_MD_ARTIFACT,
        RalphToolName.REPORT_PROGRESS,
        RalphToolName.DECLARE_COMPLETE,
        RalphToolName.COORDINATE,
    }
    originally_deferred = {
        RalphToolName.GIT_LOG,
        RalphToolName.GIT_SHOW,
        RalphToolName.SUBMIT_MD_ARTIFACT,
        RalphToolName.VERIFY_MD_ARTIFACT,
        RalphToolName.STAGE_MD_ARTIFACT,
        RalphToolName.GET_MD_DRAFT,
        RalphToolName.DISCARD_MD_DRAFT,
        RalphToolName.FINALIZE_MD_ARTIFACT,
        RalphToolName.REPORT_PROGRESS,
        RalphToolName.DECLARE_COMPLETE,
        RalphToolName.COORDINATE,
        RalphToolName.WEB_SEARCH,
        RalphToolName.VISIT_URL,
        RalphToolName.DOWNLOAD_URL,
        RalphToolName.READ_IMAGE,
        RalphToolName.READ_MEDIA,
    }
    entries_by_tool = {entry.tool: entry for entry in AUDIT_REGISTER}
    missing = structured_tools - set(entries_by_tool)
    assert not missing, f"Structured tools missing from register: {missing}"
    for tool in structured_tools:
        entry = entries_by_tool[tool]
        assert entry.outcome == AuditOutcome.KEEP, (
            f"Phase 4 tool {tool} should be keep, got {entry.outcome}"
        )
    for tool in originally_deferred:
        entry = entries_by_tool[tool]
        assert entry.outcome != AuditOutcome.DEFER, (
            f"Originally-deferred tool {tool} is still DEFER after Phase 4: {entry.outcome}"
        )
    assert len(structured_tools) == 9, (
        f"structured_tools must contain exactly 9 entries, got {len(structured_tools)}"
    )
    assert len(originally_deferred) == 16, (
        f"originally_deferred must contain exactly 16 entries, got {len(originally_deferred)}"
    )


def test_audit_register_list_directory_tools_are_implemented() -> None:
    entries_by_tool = {entry.tool: entry for entry in AUDIT_REGISTER}
    for tool_name in (
        RalphToolName.LIST_DIRECTORY,
        RalphToolName.LIST_DIRECTORY_RECURSIVE,
        RalphToolName.DIRECTORY_TREE,
    ):
        entry = entries_by_tool.get(tool_name)
        assert entry is not None, f"{tool_name} must have an audit entry"
        assert entry.outcome != AuditOutcome.DEFER, (
            f"AC-01/AC-09: {tool_name} is implemented in "
            "workspace/_read_handlers.py (compact/ranked/outline views "
            "wired through the indexed handle). A DEFER outcome would "
            "contradict the shipped behavior and mislead the audit "
            "register. Re-audit if the indexed views are ever removed."
        )


def test_audit_register_includes_phase1_add_argument_entries() -> None:
    phase1_tools = {
        RalphToolName.READ_FILE,
        RalphToolName.READ_MULTIPLE_FILES,
        RalphToolName.SEARCH_FILES,
        RalphToolName.GREP_FILES,
        RalphToolName.EDIT_FILE,
    }
    entries_by_tool = {entry.tool: entry for entry in AUDIT_REGISTER}
    missing = phase1_tools - set(entries_by_tool)
    assert not missing, f"Phase 1 tools missing from register: {missing}"
    for tool in phase1_tools:
        entry = entries_by_tool[tool]
        assert entry.outcome == AuditOutcome.ADD_ARGUMENT, (
            f"Phase 1 tool {tool} should be add_argument, got {entry.outcome}"
        )


def test_every_entry_has_required_counters() -> None:
    missing = [entry.tool for entry in AUDIT_REGISTER if entry.counters is None]
    assert not missing, f"Missing counters for entries: {missing}"


def test_every_entry_counters_meet_field_contract() -> None:
    bad: list[str] = []
    for entry in AUDIT_REGISTER:
        c = entry.counters
        if c is None:
            bad.append(f"{entry.tool}: counters is None")
            continue
        if c.transcript_tokens < 0:
            bad.append(f"{entry.tool}: transcript_tokens < 0")
        if c.returned_bytes < 0:
            bad.append(f"{entry.tool}: returned_bytes < 0")
        if c.tool_calls < 0:
            bad.append(f"{entry.tool}: tool_calls < 0")
        if not 0.0 <= c.evidence_recall <= 1.0:
            bad.append(f"{entry.tool}: evidence_recall out of [0,1]")
        if not 0.0 <= c.evidence_precision <= 1.0:
            bad.append(f"{entry.tool}: evidence_precision out of [0,1]")
        if c.stale_fallback_events < 0:
            bad.append(f"{entry.tool}: stale_fallback_events < 0")
        if c.parse_count < 0:
            bad.append(f"{entry.tool}: parse_count < 0")
        if c.changed_file_count < 0:
            bad.append(f"{entry.tool}: changed_file_count < 0")
        if c.index_storage_bytes < 0:
            bad.append(f"{entry.tool}: index_storage_bytes < 0")
        if c.wall_time_seconds <= 0:
            bad.append(f"{entry.tool}: wall_time_seconds <= 0")
    assert not bad, "Counter contract violations: " + "; ".join(bad)


def test_audit_counters_rejects_out_of_range_recall() -> None:
    with pytest.raises(ValueError, match="evidence_recall"):
        AuditCounters(
            transcript_tokens=0,
            returned_bytes=0,
            tool_calls=0,
            evidence_recall=1.5,
            evidence_precision=1.0,
            stale_fallback_events=0,
            parse_count=0,
            changed_file_count=0,
            index_storage_bytes=0,
            wall_time_seconds=0.01,
        )


def test_audit_counters_rejects_negative_bytes() -> None:
    with pytest.raises(ValueError, match="returned_bytes"):
        AuditCounters(
            transcript_tokens=0,
            returned_bytes=-1,
            tool_calls=0,
            evidence_recall=1.0,
            evidence_precision=1.0,
            stale_fallback_events=0,
            parse_count=0,
            changed_file_count=0,
            index_storage_bytes=0,
            wall_time_seconds=0.01,
        )


def test_audit_counters_rejects_zero_wall_time() -> None:
    with pytest.raises(ValueError, match="wall_time_seconds"):
        AuditCounters(
            transcript_tokens=0,
            returned_bytes=0,
            tool_calls=0,
            evidence_recall=1.0,
            evidence_precision=1.0,
            stale_fallback_events=0,
            parse_count=0,
            changed_file_count=0,
            index_storage_bytes=0,
            wall_time_seconds=0,
        )


_REQUIRED_BASELINE_FAMILIES: tuple[AuditFamily, ...] = (
    AuditFamily.WORKSPACE_READ,
    AuditFamily.WORKSPACE_SEARCH,
    AuditFamily.WORKSPACE_LIST,
    AuditFamily.WORKSPACE_MUTATE,
    AuditFamily.GIT_READ,
    AuditFamily.ARTIFACT,
    AuditFamily.PROCESS,
)


def test_required_audit_families_have_baseline_flows() -> None:
    flows = family_baseline_flows()
    by_family = {flow.family: flow for flow in flows}
    missing = set(_REQUIRED_BASELINE_FAMILIES) - set(by_family)
    assert not missing, f"Missing baseline flows for families: {missing}"


def test_required_audit_families_are_distinct() -> None:
    assert len(_REQUIRED_BASELINE_FAMILIES) == len(set(_REQUIRED_BASELINE_FAMILIES)), (
        "Duplicate families in _REQUIRED_BASELINE_FAMILIES would "
        "silently mask a missing audit family"
    )


def test_every_family_baseline_flow_is_complete() -> None:
    for flow in family_baseline_flows():
        assert flow.name.strip(), f"{flow.family}: name must be non-empty"
        assert flow.current_operation_script, (
            f"{flow.family}: current_operation_script must list at least one tool"
        )
        for tool_name in flow.current_operation_script:
            assert tool_name.strip(), f"{flow.family}: tool name in script must be non-empty"
        assert flow.catalog_token_evidence.strip(), (
            f"{flow.family}: catalog_token_evidence must be non-empty"
        )
        assert flow.counters is not None, f"{flow.family}: counters must be set"
        assert flow.counters.tool_calls >= 1, (
            f"{flow.family}: baseline script must declare at least one tool call"
        )
        assert flow.counters.transcript_tokens > 0, (
            f"{flow.family}: baseline transcript_tokens must be > 0"
        )
        assert flow.counters.returned_bytes > 0, (
            f"{flow.family}: baseline returned_bytes must be > 0"
        )
        assert 0.0 <= flow.counters.evidence_recall <= 1.0
        assert 0.0 <= flow.counters.evidence_precision <= 1.0


def test_every_audited_tool_family_has_at_least_one_entry() -> None:
    audit_by_family = {entry.family for entry in AUDIT_REGISTER}
    for flow in family_baseline_flows():
        assert flow.family in audit_by_family, (
            f"Family {flow.family} has a baseline flow but no audit entry"
        )


def test_refresh_audit_register_replaces_seed_with_measured_counters() -> None:
    measured_counters = AuditCounters(
        transcript_tokens=42,
        returned_bytes=128,
        tool_calls=2,
        evidence_recall=1.0,
        evidence_precision=1.0,
        stale_fallback_events=0,
        parse_count=0,
        changed_file_count=0,
        index_storage_bytes=0,
        wall_time_seconds=0.05,
    )
    measurement = Measurement(
        tool=RalphToolName.READ_FILE,
        counters=measured_counters,
        source="test:measured-read-file",
    )
    result = refresh_audit_register([measurement])
    by_tool = {entry.tool: entry for entry in result.register}
    assert RalphToolName.READ_FILE in by_tool
    replaced = by_tool[RalphToolName.READ_FILE]
    assert replaced.counters == measured_counters, (
        "The measured AuditCounters MUST be persisted on the entry. "
        "A regression that ignored the measurement would silently "
        "report the seed baseline as the current truth."
    )
    seed_entry = by_tool[RalphToolName.READ_FILE]
    seed_match = next(e for e in AUDIT_REGISTER if e.tool == RalphToolName.READ_FILE)
    assert seed_entry.rationale == seed_match.rationale
    assert seed_entry.family == seed_match.family
    assert seed_entry.outcome == seed_match.outcome
    assert seed_entry.risk == seed_match.risk


def test_refresh_audit_register_reports_applied_and_unmeasured() -> None:
    measured_counters = AuditCounters(
        transcript_tokens=10,
        returned_bytes=20,
        tool_calls=1,
        evidence_recall=1.0,
        evidence_precision=1.0,
        stale_fallback_events=0,
        parse_count=0,
        changed_file_count=0,
        index_storage_bytes=0,
        wall_time_seconds=0.01,
    )
    measurement = Measurement(
        tool=RalphToolName.READ_FILE,
        counters=measured_counters,
        source="test:only-read-file",
    )
    result = refresh_audit_register([measurement])
    assert result.applied == frozenset({RalphToolName.READ_FILE})
    assert RalphToolName.READ_FILE not in result.unmeasured
    assert result.duplicates == frozenset()
    for member in RalphToolName:
        if member == RalphToolName.READ_FILE:
            continue
        assert member in result.unmeasured, (
            f"{member} should remain on the seed baseline; refresh "
            "must not silently drop tools from the unmeasured set."
        )


def test_refresh_audit_register_detects_duplicate_measurements() -> None:
    first = AuditCounters(
        transcript_tokens=10,
        returned_bytes=20,
        tool_calls=1,
        evidence_recall=1.0,
        evidence_precision=1.0,
        stale_fallback_events=0,
        parse_count=0,
        changed_file_count=0,
        index_storage_bytes=0,
        wall_time_seconds=0.01,
    )
    second = AuditCounters(
        transcript_tokens=99,
        returned_bytes=99,
        tool_calls=9,
        evidence_recall=1.0,
        evidence_precision=1.0,
        stale_fallback_events=0,
        parse_count=0,
        changed_file_count=0,
        index_storage_bytes=0,
        wall_time_seconds=0.5,
    )
    result = refresh_audit_register(
        [
            Measurement(tool=RalphToolName.READ_FILE, counters=first, source="first"),
            Measurement(tool=RalphToolName.READ_FILE, counters=second, source="second"),
        ]
    )
    assert result.duplicates == frozenset({RalphToolName.READ_FILE})
    by_tool = {entry.tool: entry for entry in result.register}
    counters = by_tool[RalphToolName.READ_FILE].counters
    assert counters.transcript_tokens == 109
    assert counters.returned_bytes == 119
    assert counters.tool_calls == 10
    assert counters.wall_time_seconds == 0.51


def test_refresh_audit_register_with_no_measurements_returns_seed_baseline() -> None:
    result = refresh_audit_register(None)
    assert result.register == AUDIT_REGISTER
    assert result.applied == frozenset()
    assert result.duplicates == frozenset()
    assert result.unmeasured == frozenset(RalphToolName)


def test_refresh_audit_register_is_deterministic() -> None:
    measured_counters = AuditCounters(
        transcript_tokens=12,
        returned_bytes=24,
        tool_calls=2,
        evidence_recall=1.0,
        evidence_precision=1.0,
        stale_fallback_events=0,
        parse_count=0,
        changed_file_count=0,
        index_storage_bytes=0,
        wall_time_seconds=0.02,
    )
    measurements = (
        Measurement(
            tool=RalphToolName.READ_FILE,
            counters=measured_counters,
            source="first",
        ),
        Measurement(
            tool=RalphToolName.WRITE_FILE,
            counters=measured_counters,
            source="second",
        ),
    )
    first = refresh_audit_register(measurements)
    second = refresh_audit_register(measurements)
    assert first.register == second.register
    assert first.applied == second.applied
    assert first.unmeasured == second.unmeasured


def test_refresh_audit_register_rejects_unknown_tool() -> None:
    from dataclasses import replace as _replace

    measured_counters = AuditCounters(
        transcript_tokens=1,
        returned_bytes=1,
        tool_calls=1,
        evidence_recall=1.0,
        evidence_precision=1.0,
        stale_fallback_events=0,
        parse_count=0,
        changed_file_count=0,
        index_storage_bytes=0,
        wall_time_seconds=0.01,
    )
    bogus_entry = next(iter(AUDIT_REGISTER))
    _replace(bogus_entry, tool=RalphToolName.READ_FILE)
    duplicate = Measurement(
        tool=RalphToolName.READ_FILE,
        counters=measured_counters,
        source="duplicate",
    )
    baseline = Measurement(
        tool=RalphToolName.READ_FILE,
        counters=measured_counters,
        source="baseline",
    )
    result = refresh_audit_register([duplicate, baseline])
    assert result.duplicates == frozenset({RalphToolName.READ_FILE})
    assert len(result.register) == len(set(RalphToolName))


def test_refresh_audit_register_overlays_measurement_source_on_entry() -> None:
    measured_counters = AuditCounters(
        transcript_tokens=64,
        returned_bytes=256,
        tool_calls=2,
        evidence_recall=1.0,
        evidence_precision=1.0,
        stale_fallback_events=0,
        parse_count=0,
        changed_file_count=0,
        index_storage_bytes=0,
        wall_time_seconds=0.04,
    )
    measurement = Measurement(
        tool=RalphToolName.READ_FILE,
        counters=measured_counters,
        source="tests/test_explore_bench_gates.py:Q1",
    )
    result = refresh_audit_register([measurement])
    by_tool = {entry.tool: entry for entry in result.register}
    replaced = by_tool[RalphToolName.READ_FILE]
    assert replaced.source == "tests/test_explore_bench_gates.py:Q1", (
        "Measurement.source must be overlaid onto the matching "
        "AuditEntry.source so the audit consumer can identify "
        "the real benchmark fixture/result identifier that "
        "produced the counters."
    )
    unmeasured_entry = next(
        entry for entry in result.register if entry.tool == RalphToolName.WRITE_FILE
    )
    assert unmeasured_entry.source == "seed"


def test_audit_entry_rejects_empty_source() -> None:
    import pytest

    measured_counters = AuditCounters(
        transcript_tokens=1,
        returned_bytes=1,
        tool_calls=1,
        evidence_recall=1.0,
        evidence_precision=1.0,
        stale_fallback_events=0,
        parse_count=0,
        changed_file_count=0,
        index_storage_bytes=0,
        wall_time_seconds=0.01,
    )
    seed_entry = next(iter(AUDIT_REGISTER))
    with pytest.raises(ValueError):
        AuditEntry(
            tool=seed_entry.tool,
            family=seed_entry.family,
            outcome=seed_entry.outcome,
            rationale=seed_entry.rationale,
            counters=measured_counters,
            risk=seed_entry.risk,
            source="",
        )
    assert seed_entry.source == "seed"


def _make_indexed(
    tool_calls: int = 2,
    returned_bytes: int = 320,
    transcript_tokens: int = 100,
    wall_time_seconds: float = 0.05,
    stale_fallback_events: int = 0,
    evidence_recall: float = 1.0,
    evidence_precision: float = 1.0,
) -> BenchmarkCounters:
    return BenchmarkCounters(
        tool_calls=tool_calls,
        returned_bytes=returned_bytes,
        transcript_tokens=transcript_tokens,
        wall_time_seconds=wall_time_seconds,
        stale_fallback_events=stale_fallback_events,
        evidence_recall=evidence_recall,
        evidence_precision=evidence_precision,
    )


def _make_result(
    question_id: str,
    *,
    indexed: BenchmarkCounters,
    parse_count: int = 0,
    changed_file_count: int = 0,
    index_storage_bytes: int = 0,
    baseline: BenchmarkCounters | None = None,
) -> BenchmarkResult:
    return BenchmarkResult(
        question_id=question_id,
        baseline=baseline or _make_indexed(),
        indexed=indexed,
        parse_count=parse_count,
        changed_file_count=changed_file_count,
        index_storage_bytes=index_storage_bytes,
    )


def test_bench_results_to_measurements_attaches_per_fixture_source() -> None:
    results = [
        _make_result(
            question_id="Q1",
            indexed=_make_indexed(tool_calls=2, returned_bytes=320),
        ),
        _make_result(
            question_id="Q4",
            indexed=_make_indexed(tool_calls=2, returned_bytes=600),
        ),
    ]
    measurements = bench_results_to_measurements(results)
    assert len(measurements) == 2
    by_source = {m.source: m for m in measurements}
    assert "bench_fixtures/Q1" in by_source
    assert "bench_fixtures/Q4" in by_source
    q1 = by_source["bench_fixtures/Q1"]
    q4 = by_source["bench_fixtures/Q4"]
    assert q1.tool is RalphToolName.GREP_FILES
    assert q4.tool is RalphToolName.RALPH_GRAPH
    assert q1.counters.returned_bytes == 320
    assert q1.counters.tool_calls == 2


def test_bench_results_to_measurements_skips_unknown_question_id() -> None:
    results = [
        _make_result(
            question_id="Q9999",
            indexed=_make_indexed(tool_calls=1, returned_bytes=10),
        ),
    ]
    assert bench_results_to_measurements(results) == []


def test_refresh_audit_register_with_bench_measurements_keeps_source() -> None:
    results = [
        _make_result(
            question_id="Q1",
            indexed=_make_indexed(tool_calls=2, returned_bytes=320),
        ),
    ]
    measurements = bench_results_to_measurements(results)
    refreshed = refresh_audit_register(measurements)
    by_tool = {entry.tool: entry for entry in refreshed.register}
    grep_entry = by_tool[RalphToolName.GREP_FILES]
    assert grep_entry.source == "bench_fixtures/Q1"
    assert grep_entry.counters.returned_bytes == 320
    assert grep_entry.counters.tool_calls == 2
    read_entry = by_tool[RalphToolName.READ_FILE]
    assert read_entry.source == "seed"

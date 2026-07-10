"""Black-box tests for the Phase 0 audit register.

Tests cover the contract documented in
``ralph/mcp/explore/audit_register.py``:

* Every ``RalphToolName`` member has exactly one entry.
* Every entry's tool name is a member of ``RalphToolName``.
* Every defer entry has a non-empty rationale.
* Every entry has a non-null ``AuditCounters`` record with
  non-negative integer values and a recall/precision in [0.0, 1.0].
* Outcome values are restricted to the closed vocabulary.
* The register is immutable from a caller's perspective.
"""

from __future__ import annotations

import pytest

from ralph.mcp.explore.audit_register import (
    AUDIT_REGISTER,
    AuditCounters,
    AuditEntry,
    AuditFamily,
    AuditOutcome,
    audit_register,
)
from ralph.mcp.explore.family_baseline import family_baseline_flows
from ralph.mcp.tools.names import RalphToolName


def test_audit_register_covers_every_ralph_tool() -> None:
    """Every RalphToolName member must have exactly one entry."""
    entries = audit_register()
    by_tool = {entry.tool for entry in entries}
    missing = set(RalphToolName) - by_tool
    extras = by_tool - set(RalphToolName)
    assert not missing, f"Missing audit entries for: {sorted(missing)}"
    assert not extras, f"Unknown audit entries: {sorted(extras)}"


def test_audit_register_has_no_duplicate_tools() -> None:
    """The register must not contain duplicate tool entries."""
    seen: dict[RalphToolName, int] = {}
    for entry in AUDIT_REGISTER:
        seen[entry.tool] = seen.get(entry.tool, 0) + 1
    duplicates = {tool: count for tool, count in seen.items() if count > 1}
    assert not duplicates, f"Duplicate audit entries: {duplicates}"


def test_audit_outcomes_are_closed_vocabulary() -> None:
    """Each entry's outcome must be one of the closed vocabulary values."""
    allowed = set(AuditOutcome)
    bad = [entry for entry in AUDIT_REGISTER if entry.outcome not in allowed]
    assert not bad, f"Non-vocabulary outcomes: {[e.tool for e in bad]}"


def test_every_entry_has_non_empty_rationale() -> None:
    """Each entry must include a non-empty rationale."""
    bad = [entry for entry in AUDIT_REGISTER if not entry.rationale.strip()]
    assert not bad, f"Empty rationales: {[e.tool for e in bad]}"


def test_every_defer_has_non_empty_rationale() -> None:
    """Defer entries must explicitly call out the deferred rationale."""
    defer_entries = [e for e in AUDIT_REGISTER if e.outcome == AuditOutcome.DEFER]
    assert defer_entries, "Expected at least one defer entry in the Phase 0 seed"
    for entry in defer_entries:
        assert entry.rationale.strip(), f"Empty rationale for defer: {entry.tool}"


def test_every_defer_has_non_empty_risk() -> None:
    """AC-01: defer entries must include a non-empty ``risk`` field."""
    defer_entries = [e for e in AUDIT_REGISTER if e.outcome == AuditOutcome.DEFER]
    assert defer_entries, "Expected at least one defer entry in the Phase 0 seed"
    bad = [e.tool for e in defer_entries if not e.risk.strip()]
    assert not bad, f"Defer entries missing risk field: {bad}"


def test_audit_register_is_immutable_snapshot() -> None:
    """audit_register() returns the same snapshot."""
    first = audit_register()
    second = audit_register()
    assert first is second


def test_audit_entry_rejects_empty_rationale() -> None:
    """AuditEntry.__post_init__ rejects an empty rationale."""
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
    """AC-01: AuditEntry.__post_init__ rejects an empty risk on DEFER."""
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


def test_audit_register_has_at_least_one_defer_entry() -> None:
    """The seed must include defer entries for non-index tool families."""
    defer_outcomes = [e for e in AUDIT_REGISTER if e.outcome == AuditOutcome.DEFER]
    assert len(defer_outcomes) >= 10, (
        "Phase 0 audit must defer at least the planning/web/media/process "
        "tools that Phase 4 owns; got "
        f"{len(defer_outcomes)} defer entries."
    )


def test_audit_register_list_directory_tools_are_implemented() -> None:
    """The Phase 0 audit must reflect the shipped indexed list view behavior.

    Pre-fix, ``list_directory`` and ``list_directory_recursive`` were
    classified as DEFER because the compact/ranked/outline views were
    said to be gated on Phase-2 symbol data. The Phase-2 implementation
    has shipped: workspace/_read_handlers.py now wires compact/ranked/
    outline through the indexed handle, consumes Phase-2 structure
    data when available, and falls back to the raw shape under
    ``use_index=never``. The audit register is the single source of
    truth for whether a tool is implemented or deferred; a stale
    DEFER would mislead audits and benchmark gates. This test pins
    the current contract: a re-audit is required if the views are
    ever removed.
    """
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
    """The four tools targeted for Phase 1 must be add_argument outcomes."""
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
    """AC-01: every entry must have a non-null ``AuditCounters``."""
    missing = [entry.tool for entry in AUDIT_REGISTER if entry.counters is None]
    assert not missing, f"Missing counters for entries: {missing}"


def test_every_entry_counters_meet_field_contract() -> None:
    """AC-01: every counters record must satisfy the field contract."""
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
    """AuditCounters.__post_init__ rejects evidence_recall > 1.0."""
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
    """AuditCounters.__post_init__ rejects negative returned_bytes."""
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
    """AC-01: AuditCounters.__post_init__ rejects wall_time_seconds <= 0."""
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


# --- Family baseline flow records (AC-04) -------------------------------


_REQUIRED_BASELINE_FAMILIES: tuple[AuditFamily, ...] = (
    AuditFamily.WORKSPACE_READ,
    AuditFamily.WORKSPACE_LIST,
    AuditFamily.WORKSPACE_MUTATE,
    AuditFamily.GIT_READ,
    AuditFamily.ARTIFACT,
    AuditFamily.PROCESS,
)


def test_required_audit_families_have_baseline_flows() -> None:
    """The Phase 0 audit must include one deterministic representative
    baseline flow per audited family (read, list, search, edit, git,
    artifact, exec). The benchmark gate compares the indexed path
    against this baseline; missing a family is an unaudited state.
    """
    flows = family_baseline_flows()
    by_family = {flow.family: flow for flow in flows}
    missing = set(_REQUIRED_BASELINE_FAMILIES) - set(by_family)
    assert not missing, f"Missing baseline flows for families: {missing}"


def test_every_family_baseline_flow_is_complete() -> None:
    """Each baseline flow record must declare a non-empty name, a
    non-empty current-operation script, complete counters, and a
    non-empty catalog-token evidence string.
    """
    for flow in family_baseline_flows():
        assert flow.name.strip(), f"{flow.family}: name must be non-empty"
        assert flow.current_operation_script, (
            f"{flow.family}: current_operation_script must list at least one tool"
        )
        for tool_name in flow.current_operation_script:
            assert tool_name.strip(), (
                f"{flow.family}: tool name in script must be non-empty"
            )
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
    """The audit register must include at least one entry for every
    family that has a baseline flow. A baseline flow without an
    entry means the indexed path is being measured against an
    ungoverned family.
    """
    audit_by_family = {entry.family for entry in AUDIT_REGISTER}
    for flow in family_baseline_flows():
        assert flow.family in audit_by_family, (
            f"Family {flow.family} has a baseline flow but no audit entry"
        )

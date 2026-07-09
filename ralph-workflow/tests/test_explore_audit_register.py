"""Black-box tests for the Phase 0 audit register.

Tests cover the contract documented in
``ralph/mcp/explore/audit_register.py``:

* Every ``RalphToolName`` member has exactly one entry.
* Every entry's tool name is a member of ``RalphToolName``.
* Every defer entry has a non-empty rationale.
* Outcome values are restricted to the closed vocabulary.
* The register is immutable from a caller's perspective.
"""

from __future__ import annotations

import pytest

from ralph.mcp.explore.audit_register import (
    AUDIT_REGISTER,
    AuditEntry,
    AuditFamily,
    AuditOutcome,
    audit_register,
)
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
        )


def test_audit_register_has_at_least_one_defer_entry() -> None:
    """The seed must include defer entries for non-index tool families."""
    defer_outcomes = [e for e in AUDIT_REGISTER if e.outcome == AuditOutcome.DEFER]
    assert len(defer_outcomes) >= 10, (
        "Phase 0 audit must defer at least the planning/web/media/process "
        "tools that Phase 4 owns; got "
        f"{len(defer_outcomes)} defer entries."
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

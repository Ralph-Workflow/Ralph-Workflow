from __future__ import annotations

import pytest

from ralph.project_policy._guard_inventory import (
    _check_guard_inventory,
    _ratchet_declarations,
)
from ralph.project_policy.starters import (
    iter_starter_names,
    read_starter,
)

_DECLARED_RATCHET_IDS: frozenset[str] = frozenset(
    {
        "sanctioned-dynamic-boundaries",
        "suppression-count-baseline",
        "verification-budget-ratchet",
        "suite-budget-ratchet",
    }
)


def _starter_guard_rows(starter_name: str) -> list[dict[str, str]]:
    from ralph.project_policy._guard_inventory import _parse_guard_inventory

    return _parse_guard_inventory(read_starter(starter_name))


@pytest.mark.parametrize("starter_name", sorted(iter_starter_names()))
def test_every_shipped_starter_carries_seven_column_grammar(starter_name: str) -> None:
    rows = _starter_guard_rows(starter_name)
    if not rows:
        content = read_starter(starter_name)
        findings = _check_guard_inventory(content, path=starter_name, filename=starter_name)
        assert findings == [], (
            f"{starter_name} has no Guard inventory table: "
            f"{[(f.requirement_id, f.missing_evidence) for f in findings]}"
        )
        return
    for index, row in enumerate(rows, start=1):
        assert row.get("claim_scope"), (
            f"{starter_name} row {index} has an empty Claim scope cell: {row}"
        )
        assert row.get("enforcement"), (
            f"{starter_name} row {index} has an empty Enforcement cell: {row}"
        )
        assert row.get("retirement_trigger"), (
            f"{starter_name} row {index} has an empty Retirement trigger cell: {row}"
        )


def test_seven_column_grammar_keeps_shipped_corpus_clean() -> None:
    from ralph.project_policy import markers as _markers

    names = sorted(iter_starter_names())
    for name in names:
        content = read_starter(name)
        findings = _check_guard_inventory(content, path=name, filename=name)
        bad_prefixes = {
            _markers.CLAIM_SCOPE_MISSING_FINDING_ID,
            _markers.CLAIM_SCOPE_MULTI_SENTENCE_FINDING_ID,
            _markers.ENFORCEMENT_MISSING_FINDING_ID,
            _markers.ENFORCEMENT_UNKNOWN_ID_FINDING_ID,
            _markers.ENFORCEMENT_ADVISORY_NO_TRIGGER_FINDING_ID,
            _markers.RATCHET_UNCOVERED_FINDING_ID,
            _markers.CONTROL_ORPHAN_FINDING_ID,
        }
        bad = [
            f
            for f in findings
            if f.requirement_id.startswith(_markers.ID_INVENTORY + ":")
            and (
                ":empty-" in f.requirement_id
                or f.requirement_id in bad_prefixes
                or any(f.requirement_id.startswith(p) for p in bad_prefixes)
            )
        ]
        assert not bad, (
            f"{name} has unexpected inventory findings: "
            f"{[(f.requirement_id, f.missing_evidence) for f in bad]}"
        )


def test_other_starters_have_no_undeclared_ratchets() -> None:
    owners = {
        "typechecking-policy.md",
        "verification-policy.md",
        "testing-policy.md",
    }
    for name in sorted(iter_starter_names()):
        if name in owners:
            continue
        content = read_starter(name)
        declarations = _ratchet_declarations(content)
        for declaration_text, control_id in declarations:
            assert control_id in _DECLARED_RATCHET_IDS, (
                f"{name} carries a body ratchet with control id {control_id!r}: "
                f"{declaration_text!r}; the only ratchets are "
                f"{sorted(_DECLARED_RATCHET_IDS)}"
            )


def test_uncovered_ratchet_declarations_are_absent_in_corpus() -> None:
    from ralph.project_policy import markers as _markers

    for name in sorted(iter_starter_names()):
        content = read_starter(name)
        findings = _check_guard_inventory(content, path=name, filename=name)
        uncovered = [
            f
            for f in findings
            if f.requirement_id == _markers.RATCHET_UNCOVERED_FINDING_ID
            or f.requirement_id.startswith(_markers.RATCHET_UNCOVERED_FINDING_ID + ":")
        ]
        assert not uncovered, (
            f"{name} has uncovered body ratchets: {[f.requirement_id for f in uncovered]}"
        )


def test_orphan_control_ids_are_absent_in_corpus() -> None:
    from ralph.project_policy import markers as _markers

    for name in sorted(iter_starter_names()):
        content = read_starter(name)
        findings = _check_guard_inventory(content, path=name, filename=name)
        orphans = [
            f
            for f in findings
            if f.requirement_id.startswith(_markers.CONTROL_ORPHAN_FINDING_ID + ":")
        ]
        assert not orphans, (
            f"{name} has orphan Guard control ids: {[f.requirement_id for f in orphans]}"
        )


def test_missing_table_is_rejected_not_skipped() -> None:
    from ralph.project_policy import markers as _markers

    content = (
        "<!-- ralph-policy-schema: v5 -->\n"
        "<!-- ralph-policy-id: synthetic.md -->\n"
        "## Purpose\n\n"
        "This synthetic starter has no Guard inventory section.\n"
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert any(
        f.requirement_id == f"{_markers.ID_INVENTORY}:synthetic.md:missing" for f in findings
    ), [f.requirement_id for f in findings]


def test_malformed_table_header_is_rejected_not_skipped() -> None:
    from ralph.project_policy import markers as _markers

    content = (
        "<!-- ralph-policy-schema: v5 -->\n"
        "<!-- ralph-policy-id: synthetic.md -->\n"
        "## Guard inventory\n"
        "\n"
        "| Wrong | Header | Columns | Only | Four |\n"
        "|---|---|---|---|---|\n"
        "| guard | owner | a | b | c |\n"
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert any(
        f.requirement_id == f"{_markers.ID_INVENTORY}:synthetic.md:malformed" for f in findings
    ), [f.requirement_id for f in findings]


def test_case_sensitive_finding_id_comparison() -> None:
    from ralph.project_policy import markers as _markers

    assert _markers.RATCHET_UNCOVERED_FINDING_ID == "RWP-INVENTORY:RATCHET-UNCOVERED"
    assert _markers.CONTROL_ORPHAN_FINDING_ID == "RWP-INVENTORY:CONTROL-ORPHAN"
    assert _markers.CLAIM_SCOPE_MISSING_FINDING_ID == "RWP-INVENTORY:CLAIM-SCOPE-MISSING"

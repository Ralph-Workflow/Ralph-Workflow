"""Pin the typechecking starter's B9 ratchet inventory (R1 + R2).

These tests parse the SHIPPED ``typechecking-policy.md`` starter and
assert that its body carries the two plan-enumerated ratchet
declarations (R1: ``sanctioned-dynamic-boundaries``; R2:
``suppression-count-baseline``), that each declaration is linked to a
Guard inventory row whose Guard cell starts with the matching
backticked control id, and that the public readiness validator
accepts the file. The link grammar is the deterministic
``<!-- ralph-control: <id> -->`` token described in
:mod:`ralph.project_policy._guard_inventory`.
"""

from __future__ import annotations

import re

from ralph.project_policy import markers
from ralph.project_policy._guard_inventory import _check_guard_inventory
from ralph.project_policy.starters import read_starter
from ralph.project_policy.validators import _validate_existing_policy_file

_R1_ID = "sanctioned-dynamic-boundaries"
_R2_ID = "suppression-count-baseline"


def _filled_in_body(name: str) -> str:
    """Read a starter and resolve placeholders the way the remediation prompt does."""
    content = read_starter(name)
    for prefix in ("<!-- RALPH-STARTER-TEMPLATE", "<!-- REPLACE-ME"):
        while (start := content.find(prefix)) != -1:
            end = content.find("-->", start) + len("-->")
            content = content[:start] + content[end:].lstrip("\n")
    content = content.replace("RALPH-COMMAND: PROJECT-FACT-UNRESOLVED", "RALPH-COMMAND: make test")
    content = content.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
    return content


def test_body_declares_r1_control_token() -> None:
    """The R1 body declaration carries the inline ``sanctioned-dynamic-boundaries`` token."""
    content = _filled_in_body("typechecking-policy.md")
    match = re.search(
        markers.CONTROL_TOKEN_RE,
        content,
    )
    assert match, "typechecking-policy.md must carry at least one ralph-control token"
    ids = re.findall(markers.CONTROL_TOKEN_RE, content)
    assert _R1_ID in ids


def test_body_declares_r2_control_token() -> None:
    """The R2 body declaration carries the inline ``suppression-count-baseline`` token."""
    content = _filled_in_body("typechecking-policy.md")
    ids = re.findall(markers.CONTROL_TOKEN_RE, content)
    assert _R2_ID in ids


def test_guard_inventory_carries_r1_row() -> None:
    """The Guard inventory table has a row whose Guard cell starts with the R1 id."""
    content = _filled_in_body("typechecking-policy.md")
    findings = _check_guard_inventory(
        content, path="typechecking-policy.md", filename="typechecking-policy.md"
    )
    # No ratchet-uncovered / control-orphan finding for R1.
    r1_orphans = [
        f
        for f in findings
        if f.requirement_id.endswith(f":control-orphan:{_R1_ID}")
        or f.requirement_id.endswith(f":ratchet-uncovered:{_R1_ID}")
    ]
    assert not r1_orphans, r1_orphans
    # The R1 row's six cells are all non-empty. The shipped corpus
    # has five rows; a future edit that drops a cell is a
    # regression. The row's exact index may differ across edits, so
    # the assertion checks that no row-1..row-5 finding carries an
    # empty-cell suffix for ANY of the six columns.
    empty_cell_findings = [
        f
        for f in findings
        if f.requirement_id.startswith(f"{markers.ID_INVENTORY}:typechecking-policy.md:row-")
        and "-empty-" in f.requirement_id
    ]
    assert not empty_cell_findings, (
        f"R1 row has empty cells: {[f.requirement_id for f in empty_cell_findings]}"
    )


def test_guard_inventory_carries_r2_row() -> None:
    """The Guard inventory table has a row whose Guard cell starts with the R2 id."""
    content = _filled_in_body("typechecking-policy.md")
    findings = _check_guard_inventory(
        content, path="typechecking-policy.md", filename="typechecking-policy.md"
    )
    r2_orphans = [
        f
        for f in findings
        if f.requirement_id.endswith(f":control-orphan:{_R2_ID}")
        or f.requirement_id.endswith(f":ratchet-uncovered:{_R2_ID}")
    ]
    assert not r2_orphans, r2_orphans


def test_public_validator_accepts_filled_in_typechecking_starter() -> None:
    """The public validator returns zero RWP-INVENTORY findings for the filled-in starter."""
    content = _filled_in_body("typechecking-policy.md")
    findings = _validate_existing_policy_file(
        content, path="typechecking-policy.md", filename="typechecking-policy.md"
    )
    inventory_findings = [
        f for f in findings if f.requirement_id.startswith(f"{markers.ID_INVENTORY}:")
    ]
    assert not inventory_findings, (
        f"unexpected inventory findings: "
        f"{[(f.requirement_id, f.missing_evidence) for f in inventory_findings]}"
    )

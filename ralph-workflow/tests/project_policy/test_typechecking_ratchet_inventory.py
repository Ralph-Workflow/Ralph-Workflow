from __future__ import annotations

import re

from ralph.project_policy import markers
from ralph.project_policy._guard_inventory import _check_guard_inventory
from ralph.project_policy.starters import read_starter
from ralph.project_policy.validators import _validate_existing_policy_file

_R1_ID = "sanctioned-dynamic-boundaries"
_R2_ID = "suppression-count-baseline"


def _filled_in_body(name: str) -> str:
    content = read_starter(name)
    for prefix in ("<!-- RALPH-STARTER-TEMPLATE", "<!-- REPLACE-ME"):
        while (start := content.find(prefix)) != -1:
            end = content.find("-->", start) + len("-->")
            content = content[:start] + content[end:].lstrip("\n")
    content = content.replace("RALPH-COMMAND: PROJECT-FACT-UNRESOLVED", "RALPH-COMMAND: make test")
    content = content.replace("deterministic: PROJECT-FACT-UNRESOLVED", "deterministic: make test")
    content = content.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
    return content


def test_body_declares_r1_control_token() -> None:
    content = _filled_in_body("typechecking-policy.md")
    match = re.search(
        markers.CONTROL_TOKEN_RE,
        content,
    )
    assert match, "typechecking-policy.md must carry at least one ralph-control token"
    ids = re.findall(markers.CONTROL_TOKEN_RE, content)
    assert _R1_ID in ids


def test_body_declares_r2_control_token() -> None:
    content = _filled_in_body("typechecking-policy.md")
    ids = re.findall(markers.CONTROL_TOKEN_RE, content)
    assert _R2_ID in ids


def test_guard_inventory_carries_r1_row() -> None:
    content = _filled_in_body("typechecking-policy.md")
    findings = _check_guard_inventory(
        content, path="typechecking-policy.md", filename="typechecking-policy.md"
    )
    r1_orphans = [
        f
        for f in findings
        if f.requirement_id.endswith(f":control-orphan:{_R1_ID}")
        or f.requirement_id.endswith(f":ratchet-uncovered:{_R1_ID}")
    ]
    assert not r1_orphans, r1_orphans
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

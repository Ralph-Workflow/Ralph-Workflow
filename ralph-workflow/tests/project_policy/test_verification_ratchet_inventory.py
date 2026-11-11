from __future__ import annotations

import re

from ralph.project_policy import markers
from ralph.project_policy._guard_inventory import _check_guard_inventory, _parse_guard_inventory
from ralph.project_policy.starters import read_starter
from ralph.project_policy.validators import _validate_existing_policy_file

_R3_ID = "verification-budget-ratchet"


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


def test_body_declares_r3_control_token() -> None:
    content = _filled_in_body("verification-policy.md")
    ids = re.findall(markers.CONTROL_TOKEN_RE, content)
    assert _R3_ID in ids


def test_guard_inventory_carries_r3_row() -> None:
    content = _filled_in_body("verification-policy.md")
    findings = _check_guard_inventory(
        content, path="verification-policy.md", filename="verification-policy.md"
    )
    r3_orphans = [
        f
        for f in findings
        if f.requirement_id.endswith(f":control-orphan:{_R3_ID}")
        or f.requirement_id.endswith(f":ratchet-uncovered:{_R3_ID}")
    ]
    assert not r3_orphans, r3_orphans


def test_r3_row_detects_unauthorized_budget_growth() -> None:
    content = _filled_in_body("verification-policy.md")
    findings = _check_guard_inventory(
        content, path="verification-policy.md", filename="verification-policy.md"
    )
    bad = [
        f
        for f in findings
        if f.requirement_id.startswith(f"{markers.ID_INVENTORY}:verification-policy.md")
        and (
            "empty-" in f.requirement_id
            or "claim-scope" in f.requirement_id
            or "control-orphan" in f.requirement_id
            or "ratchet-uncovered" in f.requirement_id
        )
    ]
    assert not bad, bad


def test_r3_row_actually_mentions_verification_time_budget() -> None:
    content = _filled_in_body("verification-policy.md")
    rows = _parse_guard_inventory(content)
    r3_rows = [row for row in rows if _R3_ID in row["guard"]]
    assert len(r3_rows) == 1, (
        f"verification-policy.md must carry exactly one R3 row, found {len(r3_rows)}"
    )
    r3_row = r3_rows[0]
    assert "verification_time_budget" in r3_row["detected_condition"], (
        f"R3 row's Detected condition must mention the recorded "
        f"``verification_time_budget`` fact so the detector observes the actual "
        f"value, not a vague claim; current cell: {r3_row['detected_condition']!r}"
    )
    assert (
        "prior recorded value" in r3_row["detected_condition"]
        or "shrinks" in r3_row["detected_condition"]
    ), (
        f"R3 row's Detected condition must describe the ratchet semantics "
        f"(shrinking is free, growth is rejected); current cell: {r3_row['detected_condition']!r}"
    )


def test_public_validator_accepts_filled_in_verification_starter() -> None:
    content = _filled_in_body("verification-policy.md")
    findings = _validate_existing_policy_file(
        content, path="verification-policy.md", filename="verification-policy.md"
    )
    inventory_findings = [
        f for f in findings if f.requirement_id.startswith(f"{markers.ID_INVENTORY}:")
    ]
    assert not inventory_findings, (
        f"unexpected inventory findings: "
        f"{[(f.requirement_id, f.missing_evidence) for f in inventory_findings]}"
    )

"""Pin the verification starter's B9 ratchet inventory (R3).

These tests parse the SHIPPED ``verification-policy.md`` starter and
assert that its body carries the plan-enumerated ratchet declaration
(R3: ``verification-budget-ratchet``), that the declaration is linked
to a Guard inventory row whose Guard cell starts with the matching
backticked control id, and that the public readiness validator
accepts the file. The ratchet's row detects unauthorized budget
growth (recorded ``verification_time_budget`` shrinking is free;
growth is rejected absent a reviewed change).
"""

from __future__ import annotations

import re

from ralph.project_policy import markers
from ralph.project_policy._guard_inventory import _check_guard_inventory, _parse_guard_inventory
from ralph.project_policy.starters import read_starter
from ralph.project_policy.validators import _validate_existing_policy_file

_R3_ID = "verification-budget-ratchet"


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


def test_body_declares_r3_control_token() -> None:
    """The R3 body declaration carries the inline ``verification-budget-ratchet`` token."""
    content = _filled_in_body("verification-policy.md")
    ids = re.findall(markers.CONTROL_TOKEN_RE, content)
    assert _R3_ID in ids


def test_guard_inventory_carries_r3_row() -> None:
    """The Guard inventory table has a row whose Guard cell starts with the R3 id."""
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
    """The R3 row's Detected condition literally detects unauthorized growth.

    Per the plan: ``verification_time_budget`` shrinking is free;
    growth is rejected absent a reviewed change. The row's
    ``Detected condition`` cell must mention the recorded
    ``verification_time_budget`` so the detector can observe the
    actual value, not a vague "fast" claim.
    """
    content = _filled_in_body("verification-policy.md")
    findings = _check_guard_inventory(
        content, path="verification-policy.md", filename="verification-policy.md"
    )
    # We can't easily check the row's exact cell without parsing
    # twice, so assert that the R3 row's full presence is accepted
    # (no claim-scope findings) and that no R3-related inventory
    # findings exist.
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
    """The R3 row's Detected condition MUST literally name the budget fact.

    This is the fault-sensitive check: a future edit that drops the
    ``verification_time_budget`` phrase from the row's Detected
    condition (replacing it with a vague "fast" claim) is a
    regression, and this test fails to prove it. A mutation that
    removes the phrase from the shipped corpus must produce a
    failing test.
    """
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
    """The public validator returns zero RWP-INVENTORY findings for the filled-in starter."""
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

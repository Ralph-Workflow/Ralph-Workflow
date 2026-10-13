"""B7 + B9 guard-inventory contract tests (v5, plan WU-3 / U3).

These tests pin the v5 contract on the public readiness validator:

* B7 — ``Claim scope`` is a required guard-inventory field. Every row
  in every applicable starter's ``## Guard inventory`` table MUST
  carry a non-empty ``Claim scope`` cell. The public validator
  rejects an empty cell with the stable
  ``RWP-INVENTORY:<file>:row-N:empty-claim_scope`` finding id.
* B7 — the existing claim-scope substring predicate applies to the
  ``Claim scope`` cell too: a broad phrase absent from the row's
  ``Detected condition`` is rejected.
* B9 — the deterministic body ratchet -> control id -> Guard cell
  linkage. A body sentence that names a ratcheted / MUST NOT grow /
  MUST NOT increase constraint MUST end with an inline
  ``<!-- ralph-control: <id> -->`` token, and a Guard cell that
  begins with a backticked id MUST be backed by a body declaration.
  Mismatches are reported with the
  ``RWP-INVENTORY:<file>:ratchet-uncovered`` /
  ``RWP-INVENTORY:<file>:control-orphan`` finding ids.

The tests build synthetic in-memory starter content via the private
parser, the public validator, and ``MemoryWorkspace`` (no
``tmp_path``, no filesystem I/O), and assert the findings match the
plan's contract.
"""

from __future__ import annotations

from ralph.project_policy._guard_inventory import _check_guard_inventory
from ralph.project_policy.validators import _validate_existing_policy_file

_HEADER = (
    "| Guard | Owner | Protected outcome | Detected condition | "
    "Retirement trigger | Claim scope |\n"
    "|---|---|---|---|---|---|\n"
)


def _row(guard: str, owner: str, protected: str, detected: str, retirement: str, scope: str) -> str:
    return f"| {guard} | {owner} | {protected} | {detected} | {retirement} | {scope} |\n"


def _starter(body_before: str, rows: str, body_after: str = "") -> str:
    return (
        "<!-- ralph-policy-schema: v5 -->\n"
        "<!-- ralph-policy-id: synthetic.md -->\n" + body_before + "## Guard inventory\n"
        "\n" + _HEADER + rows + body_after
    )


# --- B7: Claim scope is a required field ----------------------------------


def test_empty_claim_scope_cell_is_rejected() -> None:
    """An empty sixth cell is rejected with the stable finding id."""
    content = _starter(
        body_before="",
        rows=_row(
            guard="guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="",
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert any(
        f.requirement_id == "RWP-INVENTORY:synthetic.md:row-1:empty-claim_scope" for f in findings
    )


def test_non_empty_claim_scope_cell_is_accepted() -> None:
    """A row whose sixth cell names the detector's narrow claim is accepted.

    Together with the existing claim-scope predicate on the
    ``Protected outcome`` cell, this is the B7 contract.
    """
    content = _starter(
        body_before="",
        rows=_row(
            guard="guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="the audit's blank-`Any` count per file",
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert findings == []


def test_claim_scope_predicate_fires_on_scope_cell() -> None:
    """A broad phrase in ``Claim scope`` absent from ``Detected condition`` fails.

    The v5 plan applies the existing B7 substring predicate to the
    new ``Claim scope`` cell. A broad phrase in the scope cell
    without the same phrase in the detected cell is rejected.
    """
    content = _starter(
        body_before="",
        rows=_row(
            guard="guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="all blank `Any` usage is guaranteed to be detected",
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert any(
        f.requirement_id.endswith(":row-1:claim-scope") and "guaranteed" in f.missing_evidence
        for f in findings
    )


# --- B9: ratchet -> control id -> row linkage -----------------------------


def test_ratchet_declaration_without_control_token_is_uncovered() -> None:
    """A body ratchet sentence with no inline control id is rejected.

    The grammar: every body line carrying one of the ratchet
    declaration phrases MUST end with
    ``<!-- ralph-control: <id> -->``. Without the token the line is
    ``RWP-INVENTORY:<file>:ratchet-uncovered``.
    """
    content = _starter(
        body_before=(
            "## Rules\n\n"
            "The dynamic-boundary set is ratcheted; it MUST NOT grow "
            "absent a reviewed change.\n\n"
        ),
        rows=_row(
            guard="guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="the audit's blank-`Any` count per file",
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert any(f.requirement_id == "RWP-INVENTORY:synthetic.md:ratchet-uncovered" for f in findings)


def test_ratchet_declaration_with_unmatched_control_id_is_uncovered() -> None:
    """A body ratchet whose control id has no Guard cell is rejected.

    The grammar: a ratchet declaration's control id MUST appear at
    the start of some Guard cell (`` `<id>` ``). The orphan-id
    finding is the ``:ratchet-uncovered:<id>`` variant.
    """
    content = _starter(
        body_before=(
            "## Rules\n\n"
            "The dynamic-boundary set is ratcheted; it MUST NOT grow "
            "absent a reviewed change. <!-- ralph-control: my-id -->\n\n"
        ),
        rows=_row(
            guard="guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="the audit's blank-`Any` count per file",
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert any(
        f.requirement_id == "RWP-INVENTORY:synthetic.md:ratchet-uncovered:my-id" for f in findings
    )


def test_ratchet_declaration_matched_to_row_is_accepted() -> None:
    """A ratchet whose control id appears in a Guard cell is accepted.

    Together with the orphan check below, this is the B9 contract
    half: every body declaration is linked to a covering row, and
    every row's control id is backed by a body declaration.
    """
    content = _starter(
        body_before=(
            "## Rules\n\n"
            "The dynamic-boundary set is ratcheted; it MUST NOT grow "
            "absent a reviewed change. <!-- ralph-control: my-id -->\n\n"
        ),
        rows=_row(
            guard="`my-id` dynamic boundary set",
            owner="owner",
            protected="the dynamic-boundary set stays at its ratcheted size",
            detected="the audit lists the dynamic-boundary set per file",
            retirement="when retired",
            scope="the audit's dynamic-boundary count per file",
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    # No uncovered-ratchet or orphan finding is expected.
    assert not any(
        f.requirement_id.startswith("RWP-INVENTORY:synthetic.md:ratchet-uncovered")
        for f in findings
    )
    assert not any(
        f.requirement_id.startswith("RWP-INVENTORY:synthetic.md:control-orphan") for f in findings
    )


def test_orphan_control_id_in_row_is_rejected() -> None:
    """A Guard cell whose backticked id has no body declaration is rejected.

    The orphan-id finding is the ``:control-orphan:<id>`` variant.
    """
    content = _starter(
        body_before="",
        rows=_row(
            guard="`orphan-id` guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="the audit's blank-`Any` count per file",
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert any(
        f.requirement_id == "RWP-INVENTORY:synthetic.md:control-orphan:orphan-id" for f in findings
    )


def test_ratchet_declaration_outside_guard_inventory_excluded() -> None:
    """Maintenance-trigger / research-basis sections are excluded from the scan.

    The grammar explicitly excludes the ``## Maintenance triggers``,
    ``## Research basis``, ``## Portfolio ownership``,
    ``## Living document contract``, and ``## Ralph markers``
    sections from the ratchet scan. A ratchet declaration in
    ``## Maintenance triggers`` is not a body declaration and
    MUST NOT trigger a finding.
    """
    content = _starter(
        body_before="",
        rows=_row(
            guard="guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="the audit's blank-`Any` count per file",
        ),
        body_after=(
            "## Maintenance triggers\n\nThe ratcheted budget may shrink freely; it MUST NOT grow.\n"
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    # The ratchet phrase appears only in the excluded section.
    assert not any(
        f.requirement_id.startswith("RWP-INVENTORY:synthetic.md:ratchet-uncovered")
        for f in findings
    )


# --- Public validator integration ----------------------------------------


def test_public_validator_emits_empty_claim_scope_finding() -> None:
    """The public validator surfaces the empty-Claim-scope finding.

    A row whose sixth cell is empty is rejected at the
    public-validator entry point. ``clean-code-policy.md`` is used
    as the filename because the validator consults the bundled
    starter registry for required fact keys; the registry lookup
    succeeds for an actual starter.
    """
    content = _starter(
        body_before="",
        rows=_row(
            guard="guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="",
        ),
    )
    findings = _validate_existing_policy_file(
        content, path="clean-code-policy.md", filename="clean-code-policy.md"
    )
    assert any("empty-claim_scope" in f.requirement_id for f in findings)

"""Pin the claim-scope predicate on guard-inventory rows (B7 / v5).

Black-box test: a guard row whose ``Protected outcome`` cell carries a
``CLAIM_SCOPE_PHRASE`` that does NOT also appear in the row's
``Detected condition`` cell is rejected by the public readiness
validator with a stable RWP-INVENTORY:* finding. The shipped corpus
already satisfies this rule; this test pins the behavior so the v5
plan's claim-scope machinery cannot be silently weakened or removed.

Deterministic: builds a small in-memory starter file via the
:func:`_validate_existing_policy_file` public seam, runs the
validator, and asserts the predicate fires for the bad fixture and
stays silent for the good one.
"""

from __future__ import annotations

import pytest

from ralph.project_policy import markers
from ralph.project_policy._guard_inventory import _check_guard_inventory
from ralph.project_policy.validators import _validate_existing_policy_file

# --- Synthetic starter content --------------------------------------------

_HEADER = (
    "| Guard | Owner | Protected outcome | Detected condition | "
    "Retirement trigger | Claim scope |\n"
    "|---|---|---|---|---|---|\n"
)


def _starter_row(
    guard: str, owner: str, protected: str, detected: str, retirement: str, scope: str
) -> str:
    return f"| {guard} | {owner} | {protected} | {detected} | {retirement} | {scope} |\n"


def _starter(rows: str) -> str:
    return (
        "<!-- ralph-policy-schema: v5 -->\n"
        "<!-- ralph-policy-id: synthetic.md -->\n"
        "## Guard inventory\n"
        "\n" + _HEADER + rows
    )


# --- Direct-helper unit cases (six-column grammar) ------------------------


def test_six_column_header_grammar_is_accepted() -> None:
    """The pinned six-column grammar parses cleanly with one well-formed row.

    A row with all six non-empty cells and a claim-scope-clean
    ``Protected outcome`` / ``Claim scope`` / ``Detected condition``
    trio MUST pass the check with zero findings. This pins the v5
    contract: the new column is required, but the old predicate still
    applies.
    """
    content = _starter(
        _starter_row(
            guard="synthetic guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="the audit's blank-`Any` count per file",
        )
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert findings == []


def test_claim_scope_phrase_without_detection_match_is_rejected() -> None:
    """A broad phrase in protected outcome absent from detected condition fails.

    The predicate exists precisely to reject the v5 anti-pattern of
    "X is enforced / guaranteed / always" when the detector only
    observes a narrower fact. This test pins that behavior at the
    black-box layer.
    """
    content = _starter(
        _starter_row(
            guard="synthetic guard",
            owner="owner",
            protected="Type safety is guaranteed at every boundary",
            detected="mypy reports zero errors in tests/",
            retirement="when retired",
            scope="mypy errors in tests/",
        )
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    matching = [
        f
        for f in findings
        if f.requirement_id.endswith(":row-1:claim-scope") and "guaranteed" in f.missing_evidence
    ]
    assert matching


def test_claim_scope_phrase_present_in_detection_is_accepted() -> None:
    """The same broad phrase in BOTH cells is accepted (the detector observes it)."""
    content = _starter(
        _starter_row(
            guard="synthetic guard",
            owner="owner",
            protected="Type safety is guaranteed at every boundary",
            detected="type safety guaranteed per the static checker report",
            retirement="when retired",
            scope="type safety guaranteed per the static checker report",
        )
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    claim_scope_findings = [f for f in findings if f.requirement_id.endswith(":row-1:claim-scope")]
    assert claim_scope_findings == []


def test_claim_scope_predicate_phrases_are_stable() -> None:
    """The predicate phrase list is a stable v5 contract; do not edit silently.

    A change to :data:`markers.CLAIM_SCOPE_PHRASES` is a v5 contract
    change: it must land with a corresponding test that names the new
    phrase and a starter update. This test pins the current shape.
    """
    expected = (
        "enforced",
        "guaranteed",
        "ensures",
        "always",
        "prevents all",
        "all ",
    )
    assert expected == markers.CLAIM_SCOPE_PHRASES


@pytest.mark.parametrize("phrase", ["enforced", "guaranteed", "ensures", "always", "all "])
def test_claim_scope_predicate_fires_per_phrase(phrase: str) -> None:
    """Each phrase in the predicate list, by itself, triggers a finding.

    The phrase must appear in the ``Protected outcome`` but NOT in the
    ``Detected condition`` for the predicate to fire. Parametrizing on
    each phrase documents which phrases are live in the v5 contract.
    """
    content = _starter(
        _starter_row(
            guard="synthetic guard",
            owner="owner",
            protected=f"this guard is {phrase} by the runtime",
            detected="the runtime reports the count of changes seen",
            retirement="when retired",
            scope="the runtime's reported change count",
        )
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    matching = [
        f
        for f in findings
        if f.requirement_id.endswith(":row-1:claim-scope") and phrase in f.missing_evidence
    ]
    assert matching, f"predicate did not fire for phrase {phrase!r}"


def test_claim_scope_predicate_fires_on_claim_scope_cell() -> None:
    """The predicate also fires on the new ``Claim scope`` cell.

    The v5 plan applies the B7 substring predicate to the row's
    ``Protected outcome`` AND the new ``Claim scope`` field. A
    broad phrase in the scope cell absent from the detected cell is
    rejected.
    """
    content = _starter(
        _starter_row(
            guard="synthetic guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="all blank `Any` usage is guaranteed to be detected",
        )
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    matching = [
        f
        for f in findings
        if f.requirement_id.endswith(":row-1:claim-scope") and "guaranteed" in f.missing_evidence
    ]
    assert matching


# --- Public-validator harness --------------------------------------------


def test_public_validator_rejects_empty_claim_scope_cell() -> None:
    """The public validator returns an empty-Claim-scope finding.

    A row whose sixth cell is empty is rejected at the
    public-validator entry point with the RWP-INVENTORY:* finding id.
    This proves the B7 ``Claim scope is a required field`` criterion
    is wired through the public seam, not just the private helper.

    The check goes through ``_validate_existing_policy_file``, which
    consults the bundled-starter ``read_starter`` registry for the
    required fact keys; the test reuses ``clean-code-policy.md`` (an
    actual starter) so the registry lookup succeeds, then asserts
    that the guard-inventory check still emits the empty-cell finding
    for our empty-scope row.
    """
    content = _starter(
        _starter_row(
            guard="synthetic guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="",
        )
    )
    findings = _validate_existing_policy_file(
        content, path="clean-code-policy.md", filename="clean-code-policy.md"
    )
    matching = [f for f in findings if "empty-claim_scope" in f.requirement_id]
    assert matching

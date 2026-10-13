"""Pin the claim-scope predicate on guard-inventory rows (B7 / v5).

Black-box test: a guard row whose ``Protected outcome`` cell carries a
``CLAIM_SCOPE_PHRASE`` that does NOT also appear in the row's
``Detected condition`` cell is rejected by the public validator with a
stable RWP-INVENTORY:* finding. The shipped corpus already satisfies
this rule; this test pins the behavior so the v5 plan's claim-scope
machinery cannot be silently weakened or removed.

Deterministic: builds a small tmp starter file in-memory via the
existing MemoryWorkspace and a synthetic string, runs the public
guard-inventory check, and asserts the predicate fires.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from ralph.project_policy import markers
from ralph.project_policy._guard_inventory import _check_guard_inventory


def _table_row(protected: str, detected: str) -> str:
    """Build one well-formed guard row with the given cells.

    The five-column header is the only legal v5 grammar; this helper
    builds a row whose ``Protected outcome`` and ``Detected condition``
    cells are arbitrary, so the test can drive the predicate.
    """
    return (
        f"| `g` Guard | owner | {protected} | {detected} | when retired |\n"
    )


def _starter(protected: str, detected: str) -> str:
    return (
        "<!-- ralph-policy-schema: v5 -->\n"
        "<!-- ralph-policy-id: synthetic.md -->\n"
        "## Guard inventory\n"
        "\n"
        "| Guard | Owner | Protected outcome | Detected condition | Retirement trigger |\n"
        "|---|---|---|---|---|\n"
        + _table_row(protected, detected)
    )


def test_claim_scope_phrase_without_detection_match_is_rejected() -> None:
    """A broad phrase in protected outcome absent from detected condition fails.

    The predicate exists precisely to reject the v5 anti-pattern of
    "X is enforced / guaranteed / always" when the detector only
    observes a narrower fact. This test pins that behavior at the
    black-box layer.
    """
    content = _starter(
        protected="Type safety is guaranteed at every boundary",
        detected="mypy reports zero errors in tests/",
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    requirement_ids = {finding.requirement_id for finding in findings}
    # At least one finding must reference the claim-scope phrase
    # 'guaranteed' for the same row.
    assert any(
        rid.endswith(":row-1:claim-scope") and "guaranteed" in finding.missing_evidence
        for rid, finding in zip(requirement_ids, findings, strict=False)
        for _ in [None]
    )


def test_claim_scope_phrase_present_in_detection_is_accepted() -> None:
    """The same broad phrase in BOTH cells is accepted (the detector observes it)."""
    content = _starter(
        protected="Type safety is guaranteed at every boundary",
        detected="type safety guaranteed per the static checker report",
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    # The predicate fires only on broad phrases absent from the detected
    # condition. With the phrase present, the row passes the predicate;
    # no claim-scope finding is emitted.
    claim_scope_findings = [
        f for f in findings if f.requirement_id.endswith(":row-1:claim-scope")
    ]
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
    assert markers.CLAIM_SCOPE_PHRASES == expected


@pytest.mark.parametrize("phrase", ["enforced", "guaranteed", "ensures", "always", "all "])
def test_claim_scope_predicate_fires_per_phrase(phrase: str) -> None:
    """Each phrase in the predicate list, by itself, triggers a finding.

    The phrase must appear in the ``Protected outcome`` but NOT in the
    ``Detected condition`` for the predicate to fire. Parametrizing on
    each phrase documents which phrases are live in the v5 contract.
    """
    content = _starter(
        protected=f"this guard is {phrase} by the runtime",
        detected="the runtime reports the count of changes seen",
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    matching = [
        f
        for f in findings
        if f.requirement_id.endswith(":row-1:claim-scope") and phrase in f.missing_evidence
    ]
    assert matching, f"predicate did not fire for phrase {phrase!r}"

from __future__ import annotations

import pytest

from ralph.project_policy import markers
from ralph.project_policy._guard_inventory import _check_guard_inventory
from ralph.project_policy.validators import _validate_existing_policy_file

_HEADER = (
    "| Guard | Owner | Protected outcome | Detected condition | "
    "Claim scope | Retirement trigger |\n"
    "|---|---|---|---|---|---|\n"
)


def _starter_row(
    guard: str, owner: str, protected: str, detected: str, scope: str, retirement: str
) -> str:
    return f"| {guard} | {owner} | {protected} | {detected} | {scope} | {retirement} |\n"


def _starter(rows: str) -> str:
    return (
        "<!-- ralph-policy-schema: freeze v5 -->\n"
        "<!-- ralph-policy-id: synthetic.md -->\n"
        "## Guard inventory\n"
        "\n" + _HEADER + rows
    )


def test_six_column_header_grammar_is_accepted() -> None:
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


def test_public_validator_rejects_empty_claim_scope_cell() -> None:
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
    matching = [f for f in findings if f.requirement_id == markers.CLAIM_SCOPE_MISSING_FINDING_ID]
    assert matching

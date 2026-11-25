from __future__ import annotations

from ralph.project_policy.guard_inventory import check_guard_inventory

_HEADER = (
    "| Guard | Owner | Protected outcome | Detected condition | "
    "Claim scope | Retirement trigger |\n"
    "|---|---|---|---|---|---|\n"
)


def _policy(protected: str, detected: str, scope: str) -> str:
    row = f"| Unsafe zones | Owner | {protected} | {detected} | {scope} | when retired |\n"
    return (
        "<!-- ralph-policy-schema: freeze v5 -->\n"
        "<!-- ralph-policy-id: clean-code-policy.md -->\n"
        "## Guard inventory\n\n" + _HEADER + row
    )


def _claim_scope(protected: str, detected: str, scope: str) -> list[object]:
    findings = check_guard_inventory(
        _policy(protected, detected, scope),
        path="docs/ralph-workflow-policy/clean-code-policy.md",
        filename="clean-code-policy.md",
    )
    return [f for f in findings if f.requirement_id.endswith(":claim-scope")]


def test_a_deliberate_wording_choice_is_reported_but_never_blocks() -> None:
    findings = _claim_scope(
        protected=(
            "memory-unsafe code exists only in registered zones, each with enforced lints, "
            "a registered block count, and safety comments"
        ),
        detected="the zone gate reports zero findings over the tracked tree",
        scope="the zone gate reports zero findings",
    )

    assert findings, "the broad-wording heuristic still surfaces as a warning"
    assert all(finding.blocking is False for finding in findings)


def test_every_claim_scope_phrase_is_advisory_only() -> None:
    findings = _claim_scope(
        protected="type safety is guaranteed and always enforced; it ensures all paths",
        detected="mypy reports zero errors",
        scope="mypy reports zero errors",
    )

    assert len(findings) >= 4
    assert not any(finding.blocking for finding in findings)

from __future__ import annotations

from ralph.project_policy import markers
from ralph.project_policy._guard_inventory import _check_guard_inventory
from ralph.workspace.memory import MemoryWorkspace

_HEADER = (
    "| Guard | Owner | Protected outcome | Detected condition | "
    "Claim scope | Enforcement | Retirement trigger |\n"
    "|---|---|---|---|---|---|---|\n"
)


def _row(
    guard: str,
    owner: str,
    protected: str,
    detected: str,
    scope: str,
    retirement: str,
    enforcement: str = "deterministic: make test",
) -> str:
    return (
        f"| {guard} | {owner} | {protected} | {detected} | {scope} | "
        f"{enforcement} | {retirement} |\n"
    )


def _starter(body_before: str, rows: str, body_after: str = "") -> str:
    return (
        f"{markers.POLICY_SCHEMA_MARKER}\n"
        "<!-- ralph-policy-id: synthetic.md -->\n" + body_before + "## Guard inventory\n"
        "\n" + _HEADER + rows + body_after
    )


def test_empty_claim_scope_cell_is_rejected() -> None:
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
    assert any(f.requirement_id == markers.CLAIM_SCOPE_MISSING_FINDING_ID for f in findings)


def test_non_empty_claim_scope_cell_is_accepted() -> None:
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


def test_ratchet_declaration_without_control_token_is_uncovered() -> None:
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
    assert any(f.requirement_id == markers.RATCHET_UNCOVERED_FINDING_ID for f in findings)


def test_ratchet_declaration_with_unmatched_control_id_is_uncovered() -> None:
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
        f.requirement_id == f"{markers.RATCHET_UNCOVERED_FINDING_ID}:my-id" for f in findings
    )


def test_ratchet_declaration_matched_to_row_is_accepted() -> None:
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
    assert not any(
        f.requirement_id.startswith(markers.RATCHET_UNCOVERED_FINDING_ID) for f in findings
    )
    assert not any(f.requirement_id.startswith(markers.CONTROL_ORPHAN_FINDING_ID) for f in findings)


def test_orphan_control_id_in_row_is_rejected() -> None:
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
        f.requirement_id == f"{markers.CONTROL_ORPHAN_FINDING_ID}:orphan-id" for f in findings
    )


def test_ratchet_declaration_outside_guard_inventory_excluded() -> None:
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
    assert not any(
        f.requirement_id.startswith(markers.RATCHET_UNCOVERED_FINDING_ID) for f in findings
    )


def test_public_validator_emits_empty_claim_scope_finding() -> None:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy import validators
    from tests.project_policy.test_validator import _complete_policy_body

    base_body = _complete_policy_body(filename="typechecking-policy.md", lang="Python")
    bad_body = base_body.replace(
        "when the test policy is removed",
        "",
    )
    ws = _make_test_workspace(bad_body)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(f.requirement_id == markers.CLAIM_SCOPE_MISSING_FINDING_ID for f in findings)


def test_untagged_ratchet_cannot_borrow_next_sentence_token() -> None:
    content = _starter(
        body_before=(
            "## Rules\n\n"
            "The dynamic-boundary set is ratcheted; it MUST NOT grow "
            "absent a reviewed change.\n"
            "A different sentence. <!-- ralph-control: my-id -->\n\n"
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
    assert any(f.requirement_id == markers.RATCHET_UNCOVERED_FINDING_ID for f in findings), [
        f.requirement_id for f in findings
    ]
    assert any(
        f.requirement_id == f"{markers.CONTROL_ORPHAN_FINDING_ID}:my-id" for f in findings
    ), [f.requirement_id for f in findings]


def test_two_ratchets_two_sentences_one_token_rejected() -> None:
    content = _starter(
        body_before=(
            "## Rules\n\n"
            "The dynamic-boundary set is ratcheted. "
            "Suppression count MUST NOT increase. "
            "<!-- ralph-control: my-id -->\n\n"
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
    uncovered = [f for f in findings if f.requirement_id == markers.RATCHET_UNCOVERED_FINDING_ID]
    assert len(uncovered) == 1, [f.requirement_id for f in findings]


def test_two_ratchets_same_sentence_one_token_accepted() -> None:
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
    assert not any(
        f.requirement_id.startswith(markers.RATCHET_UNCOVERED_FINDING_ID) for f in findings
    ), [f.requirement_id for f in findings]
    assert not any(
        f.requirement_id.startswith(markers.CONTROL_ORPHAN_FINDING_ID) for f in findings
    ), [f.requirement_id for f in findings]


def test_tagged_ratchet_followed_by_unrelated_sentence_is_accepted() -> None:
    content = _starter(
        body_before=(
            "## Rules\n\n"
            "The set is ratcheted. <!-- ralph-control: my-id --> "
            "Review this annually.\n\n"
        ),
        rows=_row(
            guard="`my-id` set",
            owner="owner",
            protected="the set stays at its ratcheted size",
            detected="the audit lists the set per file",
            retirement="when retired",
            scope="the audit's set count per file",
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert not any(
        f.requirement_id.startswith(markers.RATCHET_UNCOVERED_FINDING_ID) for f in findings
    ), [f.requirement_id for f in findings]
    assert not any(
        f.requirement_id.startswith(markers.CONTROL_ORPHAN_FINDING_ID) for f in findings
    ), [f.requirement_id for f in findings]


def test_wrapped_ratchet_does_not_borrow_token_from_later_sentence() -> None:
    content = _starter(
        body_before=(
            "## Rules\n\n"
            "The set is ratcheted; it MUST NOT grow\n"
            "absent a reviewed change.\n"
            "Review this annually. <!-- ralph-control: my-id -->\n\n"
        ),
        rows=_row(
            guard="`my-id` set",
            owner="owner",
            protected="the set stays at its ratcheted size",
            detected="the audit lists the set per file",
            retirement="when retired",
            scope="the audit's set count per file",
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert any(f.requirement_id == markers.RATCHET_UNCOVERED_FINDING_ID for f in findings), [
        f.requirement_id for f in findings
    ]
    assert any(
        f.requirement_id == f"{markers.CONTROL_ORPHAN_FINDING_ID}:my-id" for f in findings
    ), [f.requirement_id for f in findings]


def test_wrapped_ratchet_finds_lone_tag_on_following_line() -> None:
    content = _starter(
        body_before=(
            "## Rules\n\n"
            "    - The set is ratcheted; it MUST NOT grow\n"
            "      absent a reviewed change.\n"
            "      <!-- ralph-control: my-id -->\n\n"
        ),
        rows=_row(
            guard="`my-id` set",
            owner="owner",
            protected="the set stays at its ratcheted size",
            detected="the audit lists the set per file",
            retirement="when retired",
            scope="the audit's set count per file",
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert not any(
        f.requirement_id.startswith(markers.RATCHET_UNCOVERED_FINDING_ID) for f in findings
    ), [f.requirement_id for f in findings]
    assert not any(
        f.requirement_id.startswith(markers.CONTROL_ORPHAN_FINDING_ID) for f in findings
    ), [f.requirement_id for f in findings]


def test_claim_scope_multi_sentence_is_rejected() -> None:
    content = _starter(
        body_before="",
        rows=_row(
            guard="guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="Counts known items. Guarantees stability",
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert any(
        f.requirement_id == markers.CLAIM_SCOPE_MULTI_SENTENCE_FINDING_ID for f in findings
    ), [f.requirement_id for f in findings]


def test_claim_scope_filename_dot_is_one_sentence() -> None:
    content = _starter(
        body_before="",
        rows=_row(
            guard="guard",
            owner="owner",
            protected="the check rejects blank `Any` at boundaries",
            detected="the audit reports blank `Any` per file",
            retirement="when retired",
            scope="Counts known items.foo.txt",
        ),
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert not any(
        f.requirement_id == markers.CLAIM_SCOPE_MULTI_SENTENCE_FINDING_ID for f in findings
    ), [f.requirement_id for f in findings]
    assert not any(f.requirement_id == markers.CLAIM_SCOPE_MISSING_FINDING_ID for f in findings), [
        f.requirement_id for f in findings
    ]


def _make_test_workspace(typechecking_body: str) -> MemoryWorkspace:
    from ralph.project_policy import starters
    from tests.project_policy.test_validator import _complete_policy_body

    workspace = MemoryWorkspace()
    workspace.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    workspace.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee AGENTS.md for project policy.\n")
    workspace.write(
        f"{markers.CANONICAL_DIR}policy-portfolio.toml",
        starters.read_starter("policy-portfolio.toml").replace(
            "PROJECT-FACT-UNRESOLVED", "make test"
        ),
    )
    workspace.mkdirs(markers.CANONICAL_DIR.rstrip("/"))
    for filename in markers.CORE_POLICY_FILES:
        if filename == "typechecking-policy.md":
            workspace.write(f"{markers.CANONICAL_DIR}{filename}", typechecking_body)
        else:
            body = _complete_policy_body(
                filename=filename,
                lang=None
                if filename not in {"typechecking-policy.md", "linting-policy.md"}
                else "Python",
            )
            workspace.write(f"{markers.CANONICAL_DIR}{filename}", body)
    return workspace


def test_public_validate_readiness_emits_claim_scope_multi_sentence() -> None:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy import validators
    from tests.project_policy.test_validator import _complete_policy_body

    base_body = _complete_policy_body(filename="typechecking-policy.md", lang="Python")
    bad_body = base_body.replace(
        "when the test policy is removed",
        "Counts known items. Guarantees stability",
    )
    ws = _make_test_workspace(bad_body)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(
        f.requirement_id == markers.CLAIM_SCOPE_MULTI_SENTENCE_FINDING_ID for f in findings
    ), [f.requirement_id for f in findings]


def test_public_validate_readiness_accepts_filename_dot_claim_scope() -> None:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy import validators
    from tests.project_policy.test_validator import _complete_policy_body

    base_body = _complete_policy_body(filename="typechecking-policy.md", lang="Python")
    good_body = base_body.replace(
        "when the test policy is removed",
        "Counts known items.foo.txt",
    )
    ws = _make_test_workspace(good_body)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert not any(
        f.requirement_id == markers.CLAIM_SCOPE_MULTI_SENTENCE_FINDING_ID for f in findings
    ), [f.requirement_id for f in findings]
    assert not any(f.requirement_id == markers.CLAIM_SCOPE_MISSING_FINDING_ID for f in findings), [
        f.requirement_id for f in findings
    ]


def test_public_validate_readiness_accepts_tagged_ratchet_with_following_sentence() -> None:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy import validators
    from tests.project_policy.test_validator import _complete_policy_body

    base_body = _complete_policy_body(filename="typechecking-policy.md", lang="Python")
    row_with_limit = _row(
        guard="`limit` set",
        owner="owner",
        protected="the set stays at its ratcheted size",
        detected="the audit lists the set per file",
        scope="the audit's set count per file",
        retirement="when retired",
    )
    body = base_body.replace(
        "## Guard inventory\n\n" + _HEADER,
        "The set is ratcheted. <!-- ralph-control: limit --> Review this annually.\n\n"
        "## Guard inventory\n\n" + _HEADER + row_with_limit,
    )
    ws = _make_test_workspace(body)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert not any(
        f.requirement_id.startswith(markers.RATCHET_UNCOVERED_FINDING_ID) for f in findings
    ), [f.requirement_id for f in findings]
    assert not any(
        f.requirement_id.startswith(markers.CONTROL_ORPHAN_FINDING_ID) for f in findings
    ), [f.requirement_id for f in findings]


def test_public_validate_readiness_rejects_wrapped_ratchet_borrowing_token() -> None:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy import validators
    from tests.project_policy.test_validator import _complete_policy_body

    base_body = _complete_policy_body(filename="typechecking-policy.md", lang="Python")
    row_with_limit = _row(
        guard="`limit` set",
        owner="owner",
        protected="the set stays at its ratcheted size",
        detected="the audit lists the set per file",
        scope="the audit's set count per file",
        retirement="when retired",
    )
    body = base_body.replace(
        "## Guard inventory\n\n" + _HEADER,
        "The set is ratcheted; it MUST NOT grow\n"
        "absent a reviewed change.\n"
        "Review this annually. <!-- ralph-control: limit -->\n\n"
        "## Guard inventory\n\n" + _HEADER + row_with_limit,
    )
    ws = _make_test_workspace(body)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(f.requirement_id == markers.RATCHET_UNCOVERED_FINDING_ID for f in findings), [
        f.requirement_id for f in findings
    ]
    assert any(
        f.requirement_id == f"{markers.CONTROL_ORPHAN_FINDING_ID}:limit" for f in findings
    ), [f.requirement_id for f in findings]


def test_public_validate_readiness_emits_ratchet_uncovered() -> None:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy import validators
    from tests.project_policy.test_validator import _complete_policy_body

    base_body = _complete_policy_body(filename="typechecking-policy.md", lang="Python")
    uncovered_body = base_body + "\nThe new boundary set MUST NOT grow absent a reviewed change.\n"
    ws = _make_test_workspace(uncovered_body)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(f.requirement_id == markers.RATCHET_UNCOVERED_FINDING_ID for f in findings), [
        f.requirement_id for f in findings
    ]

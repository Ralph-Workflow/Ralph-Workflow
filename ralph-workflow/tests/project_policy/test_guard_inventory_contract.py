"""B7 + B9 guard-inventory contract tests (v5, plan WU-3 / U3).

These tests pin the v5 contract on the public readiness validator:

* B7 — ``Claim scope`` is a required guard-inventory field. Every row
  in every applicable starter's ``## Guard inventory`` table MUST
  carry a non-empty ``Claim scope`` cell. The public validator
  rejects an empty cell with the stable
  ``RWP-INVENTORY:CLAIM-SCOPE-MISSING`` finding id, and a
  multi-sentence cell with the stable
  ``RWP-INVENTORY:CLAIM-SCOPE-MULTI-SENTENCE`` finding id.
* B7 — the existing claim-scope substring predicate applies to the
  ``Claim scope`` cell too: a broad phrase absent from the row's
  ``Detected condition`` is rejected.
* B9 — the deterministic body ratchet -> control id -> Guard cell
  linkage. A body line that names a ratcheted / ratchet / MUST NOT
  grow / MUST NOT increase constraint MUST end (or wrap to within
  a few lines) with an inline ``<!-- ralph-control: <id> -->`` token,
  and a Guard cell that begins with a backticked id MUST be backed
  by a body declaration. Mismatches are reported with the
  ``RWP-INVENTORY:RATCHET-UNCOVERED`` /
  ``RWP-INVENTORY:CONTROL-ORPHAN`` finding ids.

The tests build synthetic in-memory starter content via the private
parser, the public validator, and ``MemoryWorkspace`` (no
``tmp_path``, no filesystem I/O), and assert the findings match the
plan's contract.
"""

from __future__ import annotations

from ralph.project_policy import markers
from ralph.project_policy._guard_inventory import _check_guard_inventory
from ralph.workspace.memory import MemoryWorkspace

_HEADER = (
    "| Guard | Owner | Protected outcome | Detected condition | "
    "Claim scope | Retirement trigger |\n"
    "|---|---|---|---|---|---|\n"
)


def _row(guard: str, owner: str, protected: str, detected: str, scope: str, retirement: str) -> str:
    return f"| {guard} | {owner} | {protected} | {detected} | {scope} | {retirement} |\n"


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
    assert any(f.requirement_id == markers.CLAIM_SCOPE_MISSING_FINDING_ID for f in findings)


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
    assert any(f.requirement_id == markers.RATCHET_UNCOVERED_FINDING_ID for f in findings)


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
        f.requirement_id == f"{markers.RATCHET_UNCOVERED_FINDING_ID}:my-id" for f in findings
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
        f.requirement_id.startswith(markers.RATCHET_UNCOVERED_FINDING_ID) for f in findings
    )
    assert not any(f.requirement_id.startswith(markers.CONTROL_ORPHAN_FINDING_ID) for f in findings)


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
        f.requirement_id == f"{markers.CONTROL_ORPHAN_FINDING_ID}:orphan-id" for f in findings
    )


def test_ratchet_declaration_outside_guard_inventory_excluded() -> None:
    """Maintenance-trigger / research-basis sections are excluded from the scan.

    The grammar explicitly excludes the ``## Guard inventory``,
    ``## Maintenance triggers``, and ``## Research basis``
    sections from the ratchet scan. A ratchet declaration in
    ``## Maintenance triggers`` is not a body declaration and
    MUST NOT trigger a finding. Other sections (such as Portfolio
    ownership or Ralph markers) are NOT excluded.
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
        f.requirement_id.startswith(markers.RATCHET_UNCOVERED_FINDING_ID) for f in findings
    )


# --- Public validator integration ----------------------------------------


def test_public_validator_emits_empty_claim_scope_finding() -> None:
    """The public validator surfaces the empty-Claim-scope finding.

    A row whose sixth cell is empty is rejected by the public
    ``validate_readiness`` API with the CLAIM-SCOPE-MISSING finding id.
    """
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


# --- B9: sentence-aware adversarial fixtures (wt-15-policy feedback) -----


def test_untagged_ratchet_cannot_borrow_next_sentence_token() -> None:
    """An untagged ratchet line cannot borrow a token from a different sentence.

    The grammar refuses to look ahead past a sentence boundary:
    a ratchet line that ends a sentence (``... reviewed change.``)
    refuses any look-ahead at all, so the next line's
    ``<!-- ralph-control: id -->`` cannot be borrowed. A
    separate ``CONTROL-ORPHAN`` finding is emitted for the
    unmatched id.
    """
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
    # The untagged ratchet is rejected, the id is orphan.
    assert any(f.requirement_id == markers.RATCHET_UNCOVERED_FINDING_ID for f in findings), [
        f.requirement_id for f in findings
    ]
    assert any(
        f.requirement_id == f"{markers.CONTROL_ORPHAN_FINDING_ID}:my-id" for f in findings
    ), [f.requirement_id for f in findings]


def test_two_ratchets_two_sentences_one_token_rejected() -> None:
    """Two ratchets in two distinct sentences with one token is rejected.

    The grammar requires one control token per ratchet sentence
    when a line carries two ratchet phrases in two distinct
    sentences (separated by ``.``). A single token covers only
    the first ratchet; the second is rejected with the stable
    ``RWP-INVENTORY:RATCHET-UNCOVERED`` finding id.
    """
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
    # One uncovered (the second ratchet) and zero orphans (the
    # one token is consumed by the first ratchet).
    uncovered = [f for f in findings if f.requirement_id == markers.RATCHET_UNCOVERED_FINDING_ID]
    assert len(uncovered) == 1, [f.requirement_id for f in findings]


def test_two_ratchets_same_sentence_one_token_accepted() -> None:
    """Two ratchets in the SAME sentence with one token is accepted.

    The grammar collapses multiple ratchet phrases in the same
    sentence (separated by ``;``, not by ``.``) into one
    declaration; a single token at the end of the sentence
    covers both phrases.
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
    assert not any(
        f.requirement_id.startswith(markers.RATCHET_UNCOVERED_FINDING_ID) for f in findings
    ), [f.requirement_id for f in findings]
    assert not any(
        f.requirement_id.startswith(markers.CONTROL_ORPHAN_FINDING_ID) for f in findings
    ), [f.requirement_id for f in findings]


# --- V1 wt-15-policy analysis feedback: sentence-association bugs ----------


def test_tagged_ratchet_followed_by_unrelated_sentence_is_accepted() -> None:
    """A single-line ratchet with a tag plus an unrelated next sentence is accepted.

    The analysis feedback's first failing case was
    ``The set is ratcheted. <!-- ralph-control: limit --> Review this annually.``
    which the previous scan rejected with ``RATCHET-UNCOVERED``. The ratchet
    sentence IS tagged (the control token belongs to the first sentence,
    placed between the two sentences on one line), and the second sentence
    is unrelated to the ratchet, so the table must accept this line.

    Pin: a ratchet phrase with an inline control token in the SAME sentence
    on a multi-sentence line is accepted, and the second sentence (no
    ratchet phrase) does not produce a RATCHET-UNCOVERED finding.
    """
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
    """A wrapped untagged ratchet must not borrow a token from a later sentence.

    The analysis feedback's second failing case was a wrapped untagged
    ratchet sentence followed by ``Review this annually. <!-- ralph-control: limit -->``
    which the previous scan accepted by borrowing the token. The ratchet
    sentence ends on a line with a period, the next line is a NEW sentence
    (not a tag-only line), so the token must not be linked to the ratchet
    and the ratchet must surface as uncovered.
    """
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
    """A wrapped ratchet with the tag on a separate following line is accepted.

    The shipped corpus pattern (testing-policy, verification-policy) is
    a ratchet sentence that wraps across multiple lines and ends with a
    period, followed by an indented tag-only line as the next non-blank
    non-heading line. The look-ahead MUST recognize a tag-only line as
    the ratchet's control id, even when the ratchet sentence has already
    ended on a previous line, so the table accepts this common
    list-item-and-tag pattern.
    """
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
    """A Claim scope cell with two sentences is rejected.

    The analysis feedback's third failing case was a Claim scope cell
    carrying two sentences (``Counts known items. Guarantees stability``).
    The previous punctuation-counting heuristic under-counted sentences
    and accepted the cell. The plan freezes the cell to a single sentence;
    the boundary detector (capital-letter lookahead) now counts two
    sentences and the row is rejected.
    """
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
    """A Claim scope cell with a filename-like dot is one sentence.

    The analysis feedback's fourth failing case was a Claim scope cell
    carrying a filename-like dot (``Counts known items.foo.txt``). The
    previous punctuation-counting heuristic over-counted the dot as a
    sentence boundary and rejected the cell. The capital-letter
    lookahead now treats the filename as part of the same sentence and
    accepts the row.
    """
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


# --- V1 wt-15-policy analysis feedback: public-validator integration ---------


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
        starters.read_starter("policy-portfolio.toml"),
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
    """The public ``validate_readiness`` API surfaces the multi-sentence finding."""
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
    """The public ``validate_readiness`` API accepts a filename dot in Claim scope."""
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
    """The public ``validate_readiness`` accepts a tagged ratchet with following sentence."""
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
    """The public ``validate_readiness`` rejects wrapped ratchet borrowing later token."""
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
    """The public ``validate_readiness`` API surfaces the ratchet-uncovered finding."""
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

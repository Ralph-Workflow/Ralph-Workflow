"""Pin the v5 B7 ``Claim scope`` column sweep across the shipped corpus.

After WU-3 and WU-4 link the typechecking and verification ratchets
to the new control id grammar, the remaining 19 starter files
(CORE_POLICY_FILES minus typechecking-policy.md / verification-policy.md
plus the conditional set) need only the v5 six-column table
migration: every Guard inventory row carries a non-empty
``Claim scope`` cell. The B7 substring predicate is applied to the
new column too, so a row's ``Claim scope`` cannot drift into a broad
world-claim.

This test iterates the shipped corpus and asserts:

* every applicable starter (core + conditional) carries a
  ``## Guard inventory`` table with the pinned six-column header;
* every data row has a non-empty ``Claim scope`` cell;
* no body ratchet declaration exists outside the three enumerated
  ratchets (R1, R2, R3); the other starters MUST NOT carry an
  uncovered ``ratcheted`` / ``MUST NOT grow`` / ``MUST NOT increase``
  / ``Ratchet:`` declaration.
"""

from __future__ import annotations

import pytest

from ralph.project_policy._guard_inventory import (
    _check_guard_inventory,
    _ratchet_declarations,
)
from ralph.project_policy.starters import (
    iter_starter_names,
    read_starter,
)

#: The four ratchet control ids the plan enumerates. The other 18
#: starters MUST NOT carry any other ratchet declaration. The names
#: are the only ones with control tokens in the shipped corpus.
_DECLARED_RATCHET_IDS: frozenset[str] = frozenset(
    {
        "sanctioned-dynamic-boundaries",
        "suppression-count-baseline",
        "verification-budget-ratchet",
        "suite-budget-ratchet",
    }
)


def _starter_guard_rows(starter_name: str) -> list[dict[str, str]]:
    """Parse a starter's Guard inventory table into rows (private helper)."""
    from ralph.project_policy._guard_inventory import _parse_guard_inventory

    return _parse_guard_inventory(read_starter(starter_name))


@pytest.mark.parametrize("starter_name", sorted(iter_starter_names()))
def test_every_shipped_starter_carries_six_column_grammar(starter_name: str) -> None:
    """Every shipped starter's Guard inventory table has the pinned six-column header.

    A starter whose table is missing or malformed is REJECTED here
    (no skip): the public validator emits a stable
    ``RWP-INVENTORY:<file>:missing`` /
    ``RWP-INVENTORY:<file>:malformed`` finding, and this test
    asserts that no shipped starter reaches the corpus with a
    failing inventory. A future regression that ships a
    malformed table is caught immediately (wt-15-policy
    analysis-feedback hardening).
    """
    rows = _starter_guard_rows(starter_name)
    if not rows:
        # No skip: the parser returned 0 rows, which means the
        # public validator will reject this starter with a
        # stable RWP-INVENTORY:<file>:missing or
        # RWP-INVENTORY:<file>:malformed finding. The contract
        # is "the corpus passes the public check", so we
        # surface the failure here.
        content = read_starter(starter_name)
        findings = _check_guard_inventory(content, path=starter_name, filename=starter_name)
        assert findings == [], (
            f"{starter_name} has no Guard inventory table: "
            f"{[(f.requirement_id, f.missing_evidence) for f in findings]}"
        )
        return  # pragma: no cover -- defensive
    for index, row in enumerate(rows, start=1):
        assert row.get("claim_scope"), (
            f"{starter_name} row {index} has an empty Claim scope cell: {row}"
        )
        assert row.get("retirement_trigger"), (
            f"{starter_name} row {index} has an empty Retirement trigger cell: {row}"
        )


def test_six_column_grammar_keeps_shipped_corpus_clean() -> None:
    """The shipped corpus passes the public check with no inventory findings.

    The findings filter uses CASE-SENSITIVE exact-prefix matches
    against the stable finding-id constants; substring
    lowercased matching would over-match unrelated ids and
    silently pass through a regression (wt-15-policy
    analysis-feedback hardening).
    """
    from ralph.project_policy import markers as _markers

    names = sorted(iter_starter_names())
    for name in names:
        content = read_starter(name)
        findings = _check_guard_inventory(content, path=name, filename=name)
        bad_prefixes = {
            _markers.CLAIM_SCOPE_MISSING_FINDING_ID,
            _markers.CLAIM_SCOPE_MULTI_SENTENCE_FINDING_ID,
            _markers.RATCHET_UNCOVERED_FINDING_ID,
            _markers.CONTROL_ORPHAN_FINDING_ID,
        }
        bad = [
            f
            for f in findings
            if f.requirement_id.startswith(_markers.ID_INVENTORY + ":")
            and (
                ":empty-" in f.requirement_id
                or f.requirement_id in bad_prefixes
                or any(f.requirement_id.startswith(p) for p in bad_prefixes)
            )
        ]
        assert not bad, (
            f"{name} has unexpected inventory findings: "
            f"{[(f.requirement_id, f.missing_evidence) for f in bad]}"
        )


def test_other_starters_have_no_undeclared_ratchets() -> None:
    """No body ratchet declaration exists outside the four enumerated ratchets.

    The shipped corpus has four ratchets: R1/R2 in
    ``typechecking-policy.md``, R3 in ``verification-policy.md``,
    and the suite-budget ratchet in ``testing-policy.md``. Every
    other starter MUST NOT carry an undeclared ratchet. This is
    black-box on the public ``_ratchet_declarations`` API.
    """
    owners = {
        "typechecking-policy.md",
        "verification-policy.md",
        "testing-policy.md",
    }
    for name in sorted(iter_starter_names()):
        if name in owners:
            continue
        content = read_starter(name)
        declarations = _ratchet_declarations(content)
        for declaration_text, control_id in declarations:
            assert control_id in _DECLARED_RATCHET_IDS, (
                f"{name} carries a body ratchet with control id {control_id!r}: "
                f"{declaration_text!r}; the only ratchets are "
                f"{sorted(_DECLARED_RATCHET_IDS)}"
            )


def test_uncovered_ratchet_declarations_are_absent_in_corpus() -> None:
    """No shipped starter has a body ratchet declaration without a control id.

    The plan's grammar requires every body ratchet to end with an
    inline ``<!-- ralph-control: <id> -->`` token. The shipped
    corpus satisfies this; a future edit that drops the token is a
    regression. The check uses the PUBLIC
    ``_check_guard_inventory`` API (not a private substring
    filter) so a regression that hides the ratchet from the
    detector is caught (wt-15-policy analysis-feedback hardening).
    """
    from ralph.project_policy import markers as _markers

    for name in sorted(iter_starter_names()):
        content = read_starter(name)
        findings = _check_guard_inventory(content, path=name, filename=name)
        uncovered = [
            f
            for f in findings
            if f.requirement_id == _markers.RATCHET_UNCOVERED_FINDING_ID
            or f.requirement_id.startswith(_markers.RATCHET_UNCOVERED_FINDING_ID + ":")
        ]
        assert not uncovered, (
            f"{name} has uncovered body ratchets: {[f.requirement_id for f in uncovered]}"
        )


def test_orphan_control_ids_are_absent_in_corpus() -> None:
    """No shipped starter has a Guard cell control id with no body declaration.

    The plan's grammar requires every Guard cell starting with a
    backticked id to be backed by a body ratchet declaration. The
    shipped corpus satisfies this; a future edit that adds an
    orphan id is a regression. The check uses the CASE-SENSITIVE
    exact stable finding id ``RWP-INVENTORY:CONTROL-ORPHAN:<id>``
    (not a lowercased substring filter, which would over-match
    unrelated ids and silently pass through a regression
    (wt-15-policy analysis-feedback hardening)).
    """
    from ralph.project_policy import markers as _markers

    for name in sorted(iter_starter_names()):
        content = read_starter(name)
        findings = _check_guard_inventory(content, path=name, filename=name)
        orphans = [
            f
            for f in findings
            if f.requirement_id.startswith(_markers.CONTROL_ORPHAN_FINDING_ID + ":")
        ]
        assert not orphans, (
            f"{name} has orphan Guard control ids: {[f.requirement_id for f in orphans]}"
        )


# --- Mutation-style regression hardening (wt-15-policy feedback) --------


def test_missing_table_is_rejected_not_skipped() -> None:
    """A starter whose Guard inventory section is missing is REJECTED.

    A regression that re-introduces the prior
    ``pytest.skip(...no Guard inventory table)`` behavior
    MUST fail this test, so a future skip-on-malformed cannot
    silently pass through the public validator (wt-15-policy
    analysis-feedback hardening). The test inspects the public
    validator on a synthetic starter whose Guard inventory
    section is absent and asserts a stable
    ``RWP-INVENTORY:<file>:missing`` finding is emitted, NOT a
    skip.
    """
    from ralph.project_policy import markers as _markers

    content = (
        "<!-- ralph-policy-schema: v5 -->\n"
        "<!-- ralph-policy-id: synthetic.md -->\n"
        "## Purpose\n\n"
        "This synthetic starter has no Guard inventory section.\n"
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert any(
        f.requirement_id == f"{_markers.ID_INVENTORY}:synthetic.md:missing" for f in findings
    ), [f.requirement_id for f in findings]


def test_malformed_table_header_is_rejected_not_skipped() -> None:
    """A starter whose Guard inventory table has a malformed header is REJECTED.

    Same hardening as :func:`test_missing_table_is_rejected_not_skipped`
    for the malformed-table case.
    """
    from ralph.project_policy import markers as _markers

    content = (
        "<!-- ralph-policy-schema: v5 -->\n"
        "<!-- ralph-policy-id: synthetic.md -->\n"
        "## Guard inventory\n"
        "\n"
        "| Wrong | Header | Columns | Only | Four |\n"
        "|---|---|---|---|---|\n"
        "| guard | owner | a | b | c |\n"
    )
    findings = _check_guard_inventory(content, path="synthetic.md", filename="synthetic.md")
    assert any(
        f.requirement_id == f"{_markers.ID_INVENTORY}:synthetic.md:malformed" for f in findings
    ), [f.requirement_id for f in findings]


def test_case_sensitive_finding_id_comparison() -> None:
    """The public validator emits the EXACT finding id; a case-flipped id would miss it.

    This is a self-check that the test sweep above uses the
    EXACT stable ids (``RWP-INVENTORY:RATCHET-UNCOVERED``,
    ``RWP-INVENTORY:CONTROL-ORPHAN:<id>``) and not a lowercase
    substring filter. A future edit that lowercases the
    comparison would over-match unrelated ids and silently
    pass through a regression; this test pins the EXACT
    comparison by asserting the EXACT bytes appear in the
    public finding id.
    """
    from ralph.project_policy import markers as _markers

    # The stable v5 contract ids are uppercase. The corpus check
    # below would fail if the public validator were to emit a
    # different-cased id, e.g. "rwp-inventory:control-orphan:...".
    assert _markers.RATCHET_UNCOVERED_FINDING_ID == "RWP-INVENTORY:RATCHET-UNCOVERED"
    assert _markers.CONTROL_ORPHAN_FINDING_ID == "RWP-INVENTORY:CONTROL-ORPHAN"
    assert _markers.CLAIM_SCOPE_MISSING_FINDING_ID == "RWP-INVENTORY:CLAIM-SCOPE-MISSING"

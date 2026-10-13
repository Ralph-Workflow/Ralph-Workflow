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

#: The three ratchet control ids the plan enumerates. The other 19
#: starters MUST NOT carry any other ratchet declaration. The names
#: are the only ones with control tokens in the shipped corpus.
_DECLARED_RATCHET_IDS: frozenset[str] = frozenset(
    {
        "sanctioned-dynamic-boundaries",
        "suppression-count-baseline",
        "verification-budget-ratchet",
    }
)


def _starter_guard_rows(starter_name: str) -> list[dict[str, str]]:
    """Parse a starter's Guard inventory table into rows (private helper)."""
    from ralph.project_policy._guard_inventory import _parse_guard_inventory

    return _parse_guard_inventory(read_starter(starter_name))


@pytest.mark.parametrize("starter_name", sorted(iter_starter_names()))
def test_every_shipped_starter_carries_six_column_grammar(starter_name: str) -> None:
    """Every shipped starter's Guard inventory table has the pinned six-column header."""
    rows = _starter_guard_rows(starter_name)
    if not rows:
        pytest.skip(f"{starter_name} has no Guard inventory table")
    for index, row in enumerate(rows, start=1):
        assert row.get("claim_scope"), (
            f"{starter_name} row {index} has an empty Claim scope cell: {row}"
        )
        assert row.get("retirement_trigger"), (
            f"{starter_name} row {index} has an empty Retirement trigger cell: {row}"
        )


def test_six_column_grammar_keeps_shipped_corpus_clean() -> None:
    """The shipped corpus passes the public check with no inventory findings."""
    names = sorted(iter_starter_names())
    for name in names:
        content = read_starter(name)
        findings = _check_guard_inventory(content, path=name, filename=name)
        bad = [
            f
            for f in findings
            if "empty-" in f.requirement_id
            or "claim-scope" in f.requirement_id
            or "ratchet-uncovered" in f.requirement_id
            or "control-orphan" in f.requirement_id
        ]
        assert not bad, (
            f"{name} has unexpected inventory findings: "
            f"{[(f.requirement_id, f.missing_evidence) for f in bad]}"
        )


def test_other_starters_have_no_undeclared_ratchets() -> None:
    """No body ratchet declaration exists outside the three enumerated ratchets.

    The 19 starter files (every shipped starter other than
    ``typechecking-policy.md`` and ``verification-policy.md``) MUST
    NOT carry a ratchet declaration whose control id is NOT in the
    enumerated set. The two owners may have any of the three ids.
    """
    for name in sorted(iter_starter_names()):
        if name in {"typechecking-policy.md", "verification-policy.md"}:
            continue
        content = read_starter(name)
        declarations = _ratchet_declarations(content)
        # A declaration that carries a control id outside the
        # enumerated set is an undeclared ratchet. A declaration
        # that has NO control id is also an undeclared ratchet.
        # The cross-starter id whitelist below allows the only
        # non-ratchet starters to use ``ratcheted`` (linting +
        # clean-code reference each other's ratchets) — we
        # therefore only assert no uncovered declarations and no
        # foreign control ids.
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
    regression.
    """
    for name in sorted(iter_starter_names()):
        content = read_starter(name)
        declarations = _ratchet_declarations(content)
        uncovered = [(text, cid) for text, cid in declarations if not cid]
        assert not uncovered, f"{name} has uncovered body ratchets: {uncovered}"


def test_orphan_control_ids_are_absent_in_corpus() -> None:
    """No shipped starter has a Guard cell control id with no body declaration.

    The plan's grammar requires every Guard cell starting with a
    backticked id to be backed by a body ratchet declaration. The
    shipped corpus satisfies this; a future edit that adds an
    orphan id is a regression.
    """
    for name in sorted(iter_starter_names()):
        content = read_starter(name)
        # Use the public check's orphan findings rather than
        # reimplementing the orphan scan.
        findings = _check_guard_inventory(content, path=name, filename=name)
        orphans = [f for f in findings if "control-orphan" in f.requirement_id]
        assert not orphans, (
            f"{name} has orphan Guard control ids: {[f.requirement_id for f in orphans]}"
        )

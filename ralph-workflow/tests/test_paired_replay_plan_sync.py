from __future__ import annotations

import pytest

from tests.doc_roots import REPOSITORY_ROOT

RALPH_TEST_CHANGE_REASONS = {
    "test_plan_copies_bound_deterministic_status_to_strong_or_moderate_evidence": (
        "PRODUCT_CRITERIA ground rule 3 allows `deterministic` only for `strong` or "
        "`moderate` rules; the comment/docstring ban and the banned-construct ban are "
        "the two bounded exceptions, so both plan copies must carry the bounded "
        "statements and reject the broad wording"
    ),
    "test_plan_copies_carry_the_banned_constructs_replay_row": (
        "the §13 table carries the banned-construct ban as the second `none`-tagged "
        "deterministic exception, so each plan copy must carry exactly one row with "
        "its enforcement id, `none` tag, arm-snapshot replay procedure, and "
        "outstanding behavior-change evidence"
    ),
}

_CANONICAL_PLAN_RELATIVE_PATH = "ralph-workflow/docs/agents/policy-v5-paired-replay-plan.md"
_MIRROR_PLAN_RELATIVE_PATH = "ralph-workflow/docs/plans/policy-v5-paired-replay-plan.md"

_PLAN_RELATIVE_PATHS = (
    _CANONICAL_PLAN_RELATIVE_PATH,
    _MIRROR_PLAN_RELATIVE_PATH,
)

_FORBIDDEN_NARRATION = (
    "v5 history entry",
    "the previous draft's",
)

_MODEL_FAMILY_ROW_GUARD = "Independent review model-family diversification"
_BANNED_CONSTRUCTS_ROW_GUARD = "Banned constructs"
_BANNED_CONSTRUCTS_ENFORCEMENT = "deterministic: banned-constructs audit (audit_banned_constructs)"
_BANNED_CONSTRUCTS_EVIDENCE_TAG = "evidence tag `none`"

_PLAN_BOUNDED_DETERMINISTIC_STATEMENTS = (
    "Only a rule tagged `strong` or `moderate` may carry a `deterministic` "
    "enforcement status; a rule tagged `none` (judgment) or `anecdotal` ships "
    "`advisory` with a review trigger, and no rule is marked `deterministic` on "
    "the strength of an enforcer that is planned but not wired.",
    "Exactly two exceptions are the absolute comment and docstring ban and the "
    "banned-construct ban, project rules tagged `none` that ship "
    "`deterministic` because their wired checkers (`audit_comments`, "
    "`audit_banned_constructs`, respectively) enforce them; each reverts to "
    "`advisory` if its enforcer is removed.",
)

_PLAN_BROAD_DETERMINISTIC_PHRASES = (
    "a `none` rule a wired deterministic gate decides ships as `deterministic`",
    "`deterministic` whatever its evidence tag",
)

_CITED_POLICY_SCHEMA_SOURCE = "ralph-workflow/ralph/project_policy/markers.py"
_CITED_POLICY_SCHEMA_SYMBOLS = (
    "SCHEMA_VERSION",
    "VERSION_SPECS",
)


def _read_bytes(relative_path: str) -> bytes:
    path = REPOSITORY_ROOT / relative_path
    assert path.is_file(), f"expected shipped document at {path}"
    return path.read_bytes()


def test_plan_copies_are_byte_identical() -> None:
    canonical = _read_bytes(_CANONICAL_PLAN_RELATIVE_PATH)
    mirror = _read_bytes(_MIRROR_PLAN_RELATIVE_PATH)
    assert canonical == mirror


@pytest.mark.parametrize("relative_path", _PLAN_RELATIVE_PATHS)
def test_plan_copies_carry_no_replaced_draft_narration(relative_path: str) -> None:
    text = _read_bytes(relative_path).decode("utf-8")
    present = [phrase for phrase in _FORBIDDEN_NARRATION if phrase in text]
    assert not present, f"{relative_path} still carries narration text: {present}"


@pytest.mark.parametrize("relative_path", _PLAN_RELATIVE_PATHS)
def test_plan_copies_carry_the_model_family_replay_row(relative_path: str) -> None:
    text = _read_bytes(relative_path).decode("utf-8")
    rows = [
        line
        for line in text.splitlines()
        if line.startswith("| ") and _MODEL_FAMILY_ROW_GUARD in line
    ]
    assert len(rows) == 1, (
        f"{relative_path} must carry exactly one §13 row for {_MODEL_FAMILY_ROW_GUARD!r}; "
        f"found {len(rows)}"
    )
    row = rows[0]
    assert "advisory:" in row and "; review:" in row, (
        f"{relative_path} §13 row for {_MODEL_FAMILY_ROW_GUARD!r} must keep an advisory "
        f"disposition with a review trigger; observed: {row!r}"
    )
    assert "review.md" in row, (
        f"{relative_path} §13 row for {_MODEL_FAMILY_ROW_GUARD!r} names no replay procedure output"
    )


@pytest.mark.parametrize("relative_path", _PLAN_RELATIVE_PATHS)
def test_plan_copies_carry_the_banned_constructs_replay_row(relative_path: str) -> None:
    text = _read_bytes(relative_path).decode("utf-8")
    rows = [
        line
        for line in text.splitlines()
        if line.startswith("| ") and _BANNED_CONSTRUCTS_ROW_GUARD in line
    ]
    assert len(rows) == 1, (
        f"{relative_path} must carry exactly one §13 row for "
        f"{_BANNED_CONSTRUCTS_ROW_GUARD!r}; found {len(rows)}"
    )
    row = rows[0]
    assert _BANNED_CONSTRUCTS_ENFORCEMENT in row, (
        f"{relative_path} §13 row for {_BANNED_CONSTRUCTS_ROW_GUARD!r} does not name "
        f"the banned-constructs enforcement id: {row!r}"
    )
    assert _BANNED_CONSTRUCTS_EVIDENCE_TAG in row, (
        f"{relative_path} §13 row for {_BANNED_CONSTRUCTS_ROW_GUARD!r} does not carry "
        f"the `none` evidence tag: {row!r}"
    )
    assert "runs the banned-constructs audit over each arm snapshot" in row, (
        f"{relative_path} §13 row for {_BANNED_CONSTRUCTS_ROW_GUARD!r} does not run "
        f"the audit over each arm snapshot: {row!r}"
    )
    assert "fail on each banned construct" in row, (
        f"{relative_path} §13 row for {_BANNED_CONSTRUCTS_ROW_GUARD!r} does not require "
        f"a failure for each banned construct: {row!r}"
    )
    assert "pass on a clean tree" in row, (
        f"{relative_path} §13 row for {_BANNED_CONSTRUCTS_ROW_GUARD!r} does not require "
        f"a clean-tree pass: {row!r}"
    )
    assert "behavior-change evidence remains outstanding at the §6 minimum" in row, (
        f"{relative_path} §13 row for {_BANNED_CONSTRUCTS_ROW_GUARD!r} does not record "
        f"the outstanding behavior-change evidence: {row!r}"
    )


@pytest.mark.parametrize("relative_path", _PLAN_RELATIVE_PATHS)
def test_plan_copies_bound_deterministic_status_to_strong_or_moderate_evidence(
    relative_path: str,
) -> None:
    text = _read_bytes(relative_path).decode("utf-8")
    normalized = " ".join(text.split())
    for statement in _PLAN_BOUNDED_DETERMINISTIC_STATEMENTS:
        assert statement in normalized, (
            f"{relative_path} does not state the bounded evidence-tag rule: {statement!r}"
        )
    for phrase in _PLAN_BROAD_DETERMINISTIC_PHRASES:
        assert phrase not in normalized, (
            f"{relative_path} still carries the broad deterministic wording: {phrase!r}"
        )


def test_cited_policy_schema_source_defines_the_cited_symbols() -> None:
    source = _read_bytes(_CITED_POLICY_SCHEMA_SOURCE).decode("utf-8")
    missing = [name for name in _CITED_POLICY_SCHEMA_SYMBOLS if name not in source]
    assert not missing, f"{_CITED_POLICY_SCHEMA_SOURCE} no longer defines: {missing}"

from __future__ import annotations

import json

import pytest

from tests.doc_roots import PACKAGE_ROOT, REPOSITORY_ROOT

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
        "outstanding behavior-change evidence. The expected enforcement cell read "
        "`deterministic: banned-constructs audit (audit_banned_constructs)`, but the "
        "verification gate declares the step label `banned constructs audit "
        "(audit_banned_constructs)` and enforcement ids resolve by exact string match "
        "against the wired step labels, so the hyphenated form named no enforcer. The "
        "expected cell is now the wired label, and the row is additionally required to "
        "name a label the gate declares"
    ),
    "test_pilot_ledger_pins_section6_r1_invalid_and_r3_failed": (
        "paired-replay §6 counts a timed-out run as a failure on the pack and only "
        "invalid runs as replaceable, so the canonical pilot ledger must pin R1 as "
        "invalid with ENOSPC and R3 as failed at wall_seconds 720 with agent_exit 124"
    ),
    "test_unblinded_scored_effects_exclude_s2": (
        "R3 failed under §6 so category S2 has no valid baseline arm; unblinded "
        "treatment-effect aggregation must exclude S2 and must not compute a scored "
        "S2 pair from the failed baseline"
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
_BANNED_CONSTRUCTS_ENFORCEMENT = "deterministic: banned constructs audit (audit_banned_constructs)"
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

_PILOT_LEDGER_PATH = PACKAGE_ROOT / "tmp" / "policy-v6-replay-runs" / "ledger.jsonl"
_PILOT_UNBLINDED_PATH = PACKAGE_ROOT / "tmp" / "policy-v6-replay-runs" / "unblinded.json"
_PILOT_R3_AGENT_EXIT_PATH = PACKAGE_ROOT / "tmp" / "policy-v6-replay-runs" / "R3" / "agent_exit"
_LEDGER_FIELDS = (
    "run_id",
    "arm",
    "category",
    "outcome",
    "wall_seconds",
    "invalid",
    "scores_path",
)
_R1_INVALID_REASON = "ENOSPC: agent commands never started; no source change (see transcript.jsonl)"
_R3_TIMEOUT_SECONDS = 720
_R3_AGENT_EXIT = 124


def _read_bytes(relative_path: str) -> bytes:
    path = REPOSITORY_ROOT / relative_path
    assert path.is_file(), f"expected shipped document at {path}"
    return path.read_bytes()


def _decode_ledger_row(line: str) -> dict[str, object]:
    decoded: object = json.loads(line)
    assert isinstance(decoded, dict), f"ledger row is not a JSON object: {line}"
    row: dict[str, object] = {}
    for key, value in decoded.items():
        assert isinstance(key, str), f"ledger row key is not a string: {line}"
        row[key] = value
    return row


def _pilot_ledger_rows() -> list[dict[str, object]]:
    assert _PILOT_LEDGER_PATH.is_file(), f"expected pilot ledger at {_PILOT_LEDGER_PATH}"
    lines = _PILOT_LEDGER_PATH.read_text(encoding="utf-8").splitlines()
    return [_decode_ledger_row(line) for line in lines if line.strip()]


def _row_by_arm(rows: list[dict[str, object]], arm: str) -> dict[str, object]:
    matches = [row for row in rows if row.get("arm") == arm]
    assert len(matches) == 1, (
        f"expected exactly one ledger row for arm {arm!r}; found {len(matches)}"
    )
    return matches[0]


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


def test_pilot_ledger_pins_section6_r1_invalid_and_r3_failed() -> None:
    rows = _pilot_ledger_rows()
    assert len(rows) == 5, f"expected five pilot ledger rows; found {len(rows)}"
    for row in rows:
        assert list(row) == list(_LEDGER_FIELDS), f"unexpected ledger row schema: {row}"

    r1 = _row_by_arm(rows, "R1")
    assert r1["outcome"] == "invalid"
    assert r1["invalid"] == _R1_INVALID_REASON
    assert "ENOSPC" in str(r1["invalid"])
    assert r1["scores_path"] is None

    r3 = _row_by_arm(rows, "R3")
    assert r3["outcome"] == "failed"
    assert r3["wall_seconds"] == _R3_TIMEOUT_SECONDS
    assert r3["invalid"] is None
    assert r3["scores_path"] is None
    assert r3["category"] == "S2"

    assert _PILOT_R3_AGENT_EXIT_PATH.is_file(), (
        f"expected R3 agent_exit pin at {_PILOT_R3_AGENT_EXIT_PATH}"
    )
    assert int(_PILOT_R3_AGENT_EXIT_PATH.read_text(encoding="utf-8").strip()) == _R3_AGENT_EXIT

    by_outcome = {row["arm"]: row["outcome"] for row in rows}
    assert by_outcome == {
        "R1": "invalid",
        "R1b": "valid",
        "R2": "valid",
        "R3": "failed",
        "R4": "valid",
    }


def test_unblinded_scored_effects_exclude_s2() -> None:
    assert _PILOT_UNBLINDED_PATH.is_file(), f"expected unblinded scores at {_PILOT_UNBLINDED_PATH}"
    decoded: object = json.loads(_PILOT_UNBLINDED_PATH.read_text(encoding="utf-8"))
    assert isinstance(decoded, dict), f"unblinded.json must be a JSON object: {decoded!r}"
    pairs = decoded.get("pairs")
    assert isinstance(pairs, dict), f"unblinded.json must carry a pairs object: {decoded!r}"
    assert "S2" not in pairs, (
        "S2 must be excluded from scored treatment effects because R3 failed under §6; "
        f"observed pairs keys: {sorted(pairs)}"
    )
    assert "S1" in pairs, f"S1 remains the evaluable scored pair; observed: {sorted(pairs)}"
    excluded = decoded.get("excluded_from_scored_treatment_effects")
    assert excluded == ["S2"], (
        "unblinded.json must record S2 as excluded from scored treatment effects; "
        f"observed: {excluded!r}"
    )
    note = decoded.get("note")
    assert isinstance(note, str) and "S2 is excluded" in note, (
        f"unblinded note must state S2 exclusion; observed: {note!r}"
    )

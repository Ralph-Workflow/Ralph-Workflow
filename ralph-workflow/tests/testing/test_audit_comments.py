"""Tests for :mod:`ralph.testing.audit_comments`.

The comment-policy gate reports banned comment shapes on NEW or CHANGED
lines only, honours an inline bypass marker, and keeps the over-commenting
rule advisory.

Every fixture below is a synthetic string, so the cases are black box: no
network, no version control, no repository file is read. The planted
banned comments live inside string literals, so this file does not flag
itself when the gate scans the repository.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.testing.audit_comments import (
    COMMENT_BYPASS_MARKER,
    RULE_CHANGE_NARRATION,
    RULE_COMMENTED_OUT_CODE,
    RULE_EMPTY_BYPASS_REASON,
    RULE_OWNERLESS_MARKER,
    RULE_RESTATES_CODE,
    RULE_REVIEWER_NOTE,
    RULE_SECTION_BANNER,
    RULE_SEVERITIES,
    SEVERITY_ADVISORY,
    SEVERITY_BLOCKING,
    CommentFinding,
    audit_comments,
    main,
    scan_comment_lines,
)

if TYPE_CHECKING:
    from pathlib import Path

#: A comment that records what the code cannot say.
_CLEAN_COMMENT = "# The upstream contract caps the payload at 64 KiB."

#: A comment that records an invariant.
_CLEAN_INVARIANT = "# The count is never negative once the loop completes."

#: A comment that records a workaround and its removal trigger.
_CLEAN_WORKAROUND = "# Workaround for the 3.11 bug; remove when 3.12 lands."

#: A comment that records intent.
_CLEAN_INTENT = "# We retry once so a transient transport blip does not fail the run."


def _rules(findings: list[CommentFinding]) -> set[str]:
    return {finding.rule_id for finding in findings}


def _scan(text: str) -> list[CommentFinding]:
    """Scan every line of *text* for banned comment shapes."""
    return scan_comment_lines("fixture.py", text, set(range(1, len(text.splitlines()) + 1)))


# --- one failing fixture per banned rule ------------------------------------


def test_change_narration_fails() -> None:
    """A comment that narrates a change is reported."""
    findings = _scan("# added for the retry budget\nx = 1\n")

    assert RULE_CHANGE_NARRATION in _rules(findings)
    assert RULE_SEVERITIES[RULE_CHANGE_NARRATION] == SEVERITY_BLOCKING


def test_restating_the_code_fails() -> None:
    """A comment that repeats the code is reported as advisory."""
    findings = _scan("# total = compute_total(items)\ntotal = compute_total(items)\n")

    assert RULE_RESTATES_CODE in _rules(findings)
    assert RULE_SEVERITIES[RULE_RESTATES_CODE] == SEVERITY_ADVISORY


def test_commented_out_code_fails() -> None:
    """A comment that holds code is reported."""
    findings = _scan("# result = compute()\n")

    assert RULE_COMMENTED_OUT_CODE in _rules(findings)
    assert RULE_SEVERITIES[RULE_COMMENTED_OUT_CODE] == SEVERITY_BLOCKING


def test_section_banner_fails() -> None:
    """A comment-only banner line is reported."""
    findings = _scan("# =====\nx = 1\n")

    assert RULE_SECTION_BANNER in _rules(findings)
    assert RULE_SEVERITIES[RULE_SECTION_BANNER] == SEVERITY_BLOCKING


def test_reviewer_note_fails() -> None:
    """A note addressed to the reviewer is reported."""
    findings = _scan("# note: check the retry budget\n")

    assert RULE_REVIEWER_NOTE in _rules(findings)
    assert RULE_SEVERITIES[RULE_REVIEWER_NOTE] == SEVERITY_BLOCKING


def test_ownerless_marker_fails() -> None:
    """A deferred-work marker with no owner and no trigger is reported."""
    findings = _scan("# TODO: refactor the parser\n")

    assert RULE_OWNERLESS_MARKER in _rules(findings)
    assert RULE_SEVERITIES[RULE_OWNERLESS_MARKER] == SEVERITY_BLOCKING


def test_empty_bypass_reason_fails() -> None:
    """A bypass marker with no reason is reported as its own finding."""
    findings = _scan(f"# {COMMENT_BYPASS_MARKER}:\n")

    assert RULE_EMPTY_BYPASS_REASON in _rules(findings)
    assert RULE_SEVERITIES[RULE_EMPTY_BYPASS_REASON] == SEVERITY_BLOCKING


# --- negative cases: every clean comment passes ----------------------------


def test_a_clean_constraint_comment_passes() -> None:
    """A comment that records a constraint passes every rule."""
    assert _scan(_CLEAN_COMMENT + "\n") == []


def test_a_clean_invariant_comment_passes() -> None:
    """A comment that records an invariant passes every rule."""
    assert _scan(_CLEAN_INVARIANT + "\n") == []


def test_a_clean_workaround_comment_passes() -> None:
    """A comment that records a workaround and its trigger passes."""
    assert _scan(_CLEAN_WORKAROUND + "\n") == []


def test_a_clean_intent_comment_passes() -> None:
    """A comment that records intent passes."""
    assert _scan(_CLEAN_INTENT + "\n") == []


def test_a_clean_module_passes() -> None:
    """A module whose comments all carry intent passes."""
    text = f'"""Module docstring."""\n\n\ndef run() -> int:\n    {_CLEAN_INTENT}\n    return 0\n'

    assert _scan(text) == []


def test_a_marker_with_an_owner_and_a_trigger_passes() -> None:
    """A deferred-work marker that names an owner and a trigger passes."""
    findings = _scan("# TODO(@alice): remove when the parser lands\n")

    assert RULE_OWNERLESS_MARKER not in _rules(findings)
    assert RULE_REVIEWER_NOTE not in _rules(findings)


def test_a_docstring_is_not_a_comment() -> None:
    """A "#" inside a docstring is never a comment."""
    text = '"""A ``#`` inside a docstring is prose."""\nx = 1\n'

    assert _scan(text) == []


def test_a_hash_inside_a_string_is_not_a_comment() -> None:
    """A "#" inside a string literal is never a comment."""
    text = 'marker = "# TODO: not a comment"\n'

    assert _scan(text) == []


# --- baseline-diff mode ----------------------------------------------------


def test_a_baseline_comment_is_not_reported() -> None:
    """A banned comment the baseline already had is pre-existing debt."""
    text = "# TODO: refactor the parser\nx = 1\n"

    findings = audit_comments({"fixture.py": text}, {"fixture.py": text})

    assert findings == []


def test_a_new_banned_comment_is_reported() -> None:
    """A banned comment on a NEW line is a regression and is reported."""
    baseline = {"fixture.py": "x = 1\n"}
    current = {"fixture.py": "x = 1\n# TODO: refactor the parser\n"}

    findings = audit_comments(baseline, current)

    assert RULE_OWNERLESS_MARKER in _rules(findings)


def test_a_changed_banned_comment_is_reported() -> None:
    """A banned comment on a CHANGED line is a regression and is reported."""
    baseline = {"fixture.py": "# TODO: old note\nx = 1\n"}
    current = {"fixture.py": "# TODO: new note\nx = 1\n"}

    findings = audit_comments(baseline, current)

    assert RULE_OWNERLESS_MARKER in _rules(findings)


def test_an_unchanged_comment_next_to_a_changed_line_is_not_reported() -> None:
    """Only the changed line is reported; its neighbours stay quiet."""
    baseline = {"fixture.py": "# TODO: refactor the parser\nx = 1\n"}
    current = {"fixture.py": "# TODO: refactor the parser\nx = 2\n"}

    findings = audit_comments(baseline, current)

    assert findings == []


def test_a_new_file_is_fully_scanned() -> None:
    """A file the baseline does not have is entirely new."""
    findings = audit_comments({}, {"fixture.py": "# TODO: refactor the parser\n"})

    assert RULE_OWNERLESS_MARKER in _rules(findings)


def test_a_non_python_file_is_not_scanned() -> None:
    """The gate reads Python source only."""
    findings = audit_comments({}, {"notes.md": "# TODO: refactor the parser\n"})

    assert findings == []


# --- the bypass marker -----------------------------------------------------


def test_the_bypass_marker_on_the_line_suppresses_the_finding() -> None:
    """An inline marker with a reason waives the comment on its own line."""
    text = f"# TODO: refactor the parser  # {COMMENT_BYPASS_MARKER}: tracked in issue 42\n"

    assert _scan(text) == []


def test_the_bypass_marker_above_the_line_suppresses_the_finding() -> None:
    """A marker on the line immediately above waives the comment below it."""
    text = f"# {COMMENT_BYPASS_MARKER}: tracked in issue 42\n# TODO: refactor the parser\n"

    assert _scan(text) == []


def test_an_empty_bypass_reason_is_not_a_bypass() -> None:
    """An empty reason waives nothing and is reported."""
    text = f"# {COMMENT_BYPASS_MARKER}:\n# TODO: refactor the parser\n"

    findings = _scan(text)

    assert RULE_EMPTY_BYPASS_REASON in _rules(findings)
    assert RULE_OWNERLESS_MARKER in _rules(findings)


def test_a_whitespace_only_bypass_reason_is_not_a_bypass() -> None:
    """A reason made of whitespace is still an empty reason."""
    text = f"# {COMMENT_BYPASS_MARKER}:   \n# TODO: refactor the parser\n"

    findings = _scan(text)

    assert RULE_EMPTY_BYPASS_REASON in _rules(findings)


def test_the_bypass_marker_waives_only_the_next_line() -> None:
    """A marker waives its own line and the line below it, and no further."""
    text = (
        f"# {COMMENT_BYPASS_MARKER}: tracked in issue 42\n"
        "# TODO: refactor the parser\n"
        "# TODO: and the other one\n"
    )

    findings = _scan(text)

    assert [finding.line for finding in findings] == [3]


# --- the CLI contract ------------------------------------------------------


def test_main_returns_one_on_a_blocking_finding(tmp_path: Path) -> None:
    """The CLI fails on a banned comment."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "mod.py").write_text("# TODO: refactor the parser\n", encoding="utf-8")

    assert main([str(root)]) == 1


def test_main_returns_zero_on_a_clean_tree(tmp_path: Path) -> None:
    """The CLI passes on a clean tree."""
    root = tmp_path / "tree"
    root.mkdir()
    (root / "mod.py").write_text(f"{_CLEAN_INTENT}\nvalue = 1\n", encoding="utf-8")

    assert main([str(root)]) == 0


def test_main_returns_zero_when_only_the_baseline_carries_the_comment(
    tmp_path: Path,
) -> None:
    """Baseline-diff mode keeps a pre-existing comment out of the report."""
    baseline = tmp_path / "baseline"
    current = tmp_path / "current"
    baseline.mkdir()
    current.mkdir()
    (baseline / "mod.py").write_text("# TODO: refactor the parser\n", encoding="utf-8")
    (current / "mod.py").write_text("# TODO: refactor the parser\n", encoding="utf-8")

    assert main([str(baseline), str(current)]) == 0


def test_main_returns_two_when_a_root_is_missing(tmp_path: Path) -> None:
    """A missing root is an error, not a silent pass."""
    root = tmp_path / "tree"
    root.mkdir()

    assert main([str(root), str(tmp_path / "absent")]) == 2

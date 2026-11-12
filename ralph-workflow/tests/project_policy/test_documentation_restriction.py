from __future__ import annotations

import re
from pathlib import Path

from ralph.testing.audit_comments import audit_paths

REPO_ROOT = Path(__file__).resolve().parents[3]
PACKAGE_ROOT = REPO_ROOT / "ralph-workflow"
PACKAGE_POLICY_ROOT = PACKAGE_ROOT / "ralph" / "project_policy" / "starters"
ROOT_POLICY_ROOT = REPO_ROOT / "docs" / "ralph-workflow-policy"

CLEAN_CODE_FILES = (
    ROOT_POLICY_ROOT / "clean-code-policy.md",
    PACKAGE_POLICY_ROOT / "clean-code-policy.md",
)
DOCUMENTATION_FILES = (
    ROOT_POLICY_ROOT / "documentation-policy.md",
    PACKAGE_POLICY_ROOT / "documentation-policy.md",
)
AGENTS_FILE = REPO_ROOT / "AGENTS.md"

VERIFY_STEPS_FILE = PACKAGE_ROOT / "ralph" / "verify_steps.py"

RESTRICTION_PHRASES = (
    "only the single owning document",
    "current facts and procedures only",
    "Docstrings are banned",
)

COMMENT_BAN_PHRASE = "Source code contains no comments"

GUARD_ROW_PHRASE = "| Comment and docstring absence |"


def _read(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def _declared_comment_audit_id() -> str:
    text = _read(VERIFY_STEPS_FILE)
    match = re.search(r'"(absolute comment and docstring policy audit \(audit_comments\))"', text)
    assert match is not None, "verify_steps.py must declare the comment audit step id"
    return match.group(1)


def test_every_required_file_states_the_documentation_restriction() -> None:
    for path in (*CLEAN_CODE_FILES, *DOCUMENTATION_FILES, AGENTS_FILE):
        text = _read(path)
        for phrase in RESTRICTION_PHRASES:
            assert phrase in text, f"{path} must state {phrase!r}"


def test_every_clean_code_file_states_the_no_comment_rule() -> None:
    for path in CLEAN_CODE_FILES:
        assert COMMENT_BAN_PHRASE in _read(path), f"{path} must ban source comments"


def test_the_guard_inventory_enforcement_id_matches_the_verify_step() -> None:
    declared = _declared_comment_audit_id()
    for path in CLEAN_CODE_FILES + DOCUMENTATION_FILES:
        if path.parent != ROOT_POLICY_ROOT:
            continue
        text = _read(path)
        assert GUARD_ROW_PHRASE in text, f"{path} must carry the comment guard row"
        assert f"deterministic: {declared}" in text, f"{path} must cite {declared!r}"


def test_the_starters_keep_an_unresolved_enforcement_placeholder() -> None:
    for path in CLEAN_CODE_FILES + DOCUMENTATION_FILES:
        if path.parent != PACKAGE_POLICY_ROOT:
            continue
        text = _read(path)
        assert GUARD_ROW_PHRASE in text, f"{path} must carry the comment guard row"
        assert "deterministic: PROJECT-FACT-UNRESOLVED" in text, f"{path} keeps the placeholder"


def test_the_marker_check_flags_a_violating_sample(tmp_path: Path) -> None:
    violating = tmp_path / "violating.md"
    violating.write_text("This paragraph records the decision behind the rule.\n", encoding="utf-8")
    clean = tmp_path / "clean.md"
    clean.write_text("This paragraph states a current fact about the package.\n", encoding="utf-8")

    findings = audit_paths(["violating.md", "clean.md"], base=tmp_path)

    assert len(findings) == 1, f"expected one finding, got {findings}"
    assert findings[0].path.endswith("violating.md")
    assert findings[0].line == 1


def test_the_marker_check_passes_a_clean_sample(tmp_path: Path) -> None:
    clean = tmp_path / "clean.md"
    clean.write_text("This paragraph states a current fact about the package.\n", encoding="utf-8")

    findings = audit_paths(["clean.md"], base=tmp_path)

    assert findings == []

from __future__ import annotations

from pathlib import Path

from ralph.testing.audit_appearance_assertion_prohibition import (
    APPEARANCE_ASSERTION_PROHIBITION,
    audit_test_files,
    audit_test_source,
    format_violations,
    main,
)


def _starter_path(name: str) -> Path:
    candidates = [
        Path("ralph-workflow/ralph/project_policy/starters") / name,
        Path("ralph/project_policy/starters") / name,
    ]
    for candidate in candidates:
        if candidate.exists():
            return candidate
    return candidates[0]


STARTER_NAMES = [
    "testing-policy.md",
    "design-system-policy.md",
    "ux-policy.md",
    "accessibility-policy.md",
]

VERDICT_AUTHORITY = (
    "Criterion 8 verdict authority: a capture-backed criterion 8 verdict is agent-produced "
    "evidence and does not close the design review lane; the named human review verdict "
    "remains required."
)


def test_all_four_starters_contain_prohibition_clause() -> None:
    for name in STARTER_NAMES:
        content = _starter_path(name).read_text(encoding="utf-8")
        assert APPEARANCE_ASSERTION_PROHIBITION in content, name


def test_all_four_starters_align_verdict_authority_statement() -> None:
    for name in STARTER_NAMES:
        content = _starter_path(name).read_text(encoding="utf-8")
        assert VERDICT_AUTHORITY in content, name
        assert "design_capture_command" in content, name
        assert "ralph://media/{artifact_id}" in content, name


def test_adr_records_renderer_scope_prompt_scope_and_review_authority() -> None:
    candidates = [
        Path("ralph-workflow/docs/architecture/adr-0002-visual-design-verification.md"),
        Path("docs/architecture/adr-0002-visual-design-verification.md"),
    ]
    for candidate in candidates:
        if candidate.exists():
            content = candidate.read_text(encoding="utf-8")
            break
    else:
        raise AssertionError("ADR-0002 is missing")

    assert "web UI only" in content
    assert "bounded declared capture command" in content
    assert "no renderer or non-web UI" in content
    assert "developer prompt guidance only" in content.lower()
    assert "requires the named human review verdict" in content


def test_audit_recognizes_appearance_assertions_and_names_remedy() -> None:
    source = '''
import unittest


class DemoTest(unittest.TestCase):
    def test_visual_proof_via_css(self) -> None:
        # proof: a UI test that asserts the css class proves the design
        # looks right.
        element = self.find("#hero")
        self.assertEqual(element.style.get("color"), "rgb(0, 0, 0)")
'''
    violations = audit_test_source(source, "tests/test_demo.py")
    assert violations, "expected the audit to flag the appearance assertion"
    message = format_violations(violations)
    assert "ralph://media/{artifact_id}" in message
    assert "appearance assertions cannot prove UI design quality" in message


def test_main_returns_zero_for_clean_tree(monkeypatch) -> None:
    monkeypatch.setattr(
        "ralph.testing.audit_appearance_assertion_prohibition.audit_test_files",
        lambda root: [],
    )
    assert main() == 0


def test_main_returns_one_when_violations_found(monkeypatch) -> None:
    from ralph.testing.audit_appearance_assertion_prohibition import AppearanceAssertionViolation

    monkeypatch.setattr(
        "ralph.testing.audit_appearance_assertion_prohibition.audit_test_files",
        lambda root: [AppearanceAssertionViolation("tests/test_x.py", 1, "demo")],
    )
    assert main() == 1


def test_audit_test_files_skips_self() -> None:
    violations = audit_test_files("ralph-workflow/tests")
    paths = {violation.path for violation in violations}
    assert "ralph-workflow/tests/test_appearance_assertion_prohibition.py" not in paths

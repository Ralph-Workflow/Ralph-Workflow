from __future__ import annotations

import textwrap

from ralph.testing.audit_appearance_assertion_prohibition import (
    APPEARANCE_ASSERTION_PROHIBITION,
    AppearanceAssertionViolation,
    audit_test_files,
    audit_test_source,
    find_appearance_assertions,
    format_violations,
)


def test_prohibition_clause_is_preserved() -> None:
    assert "appearance assertion" in APPEARANCE_ASSERTION_PROHIBITION.lower()
    assert "NOT evidence of design quality" in APPEARANCE_ASSERTION_PROHIBITION
    assert "criterion 8 verdict" in APPEARANCE_ASSERTION_PROHIBITION


def test_positive_case_flags_css_appearance_assertion_in_ui_proof() -> None:
    source = textwrap.dedent(
        """
        import unittest


        class VisualTest(unittest.TestCase):
            def test_visual_layout(self) -> None:
                # This is the proof that the UI looks good: css class matches.
                self.assertIn("primary", self.find("#hero").class_name)
        """
    )
    violations = audit_test_source(source, "tests/test_demo_visual.py")
    assert len(violations) == 1
    violation = violations[0]
    assert violation.path == "tests/test_demo_visual.py"
    assert "appearance assertions cannot prove UI design quality" in violation.message
    assert "ralph://media/{artifact_id}" in violation.message


def test_positive_case_flags_style_assertion_in_design_proof() -> None:
    source = textwrap.dedent(
        """
        def test_design_appearance() -> None:
            # proof that the page looks balanced via the style attribute.
            assert element.style["color"] == "rgb(0, 0, 0)"
        """
    )
    violations = audit_test_source(source, "tests/test_design.py")
    assert violations


def test_positive_case_flags_dom_assertion_in_screen_proof() -> None:
    source = textwrap.dedent(
        """
        def test_screen_layout() -> None:
            # proof the screen layout looks right: count DOM nodes in hero.
            assert len(self.find_all("#hero > div")) == 3
        """
    )
    violations = audit_test_source(source, "tests/test_layout.py")
    assert violations


def test_negative_case_clean_test_passes() -> None:
    source = textwrap.dedent(
        """
        import unittest


        class AccessibilityTest(unittest.TestCase):
            def test_aria_role(self) -> None:
                self.assertEqual(self.find("#hero").get("role"), "banner")
        """
    )
    assert audit_test_source(source, "tests/test_clean.py") == []


def test_negative_case_non_executable_source_is_ignored() -> None:
    source = "A css class proves the UI looks balanced in a non-test context."
    assert audit_test_source(source) == []


def test_audit_test_files_skips_self() -> None:
    violations = audit_test_files("ralph-workflow/tests")
    paths = {violation.path for violation in violations}
    assert "ralph-workflow/tests/test_appearance_assertion_prohibition.py" not in paths


def test_format_violations_produces_actionable_lines() -> None:
    violation = AppearanceAssertionViolation(
        path="tests/test_demo.py", line=12, text="self.assertIn('primary', element.css_class)"
    )
    rendered = format_violations([violation])
    assert "tests/test_demo.py:12" in rendered
    assert "ralph://media/{artifact_id}" in rendered


def test_find_appearance_assertions_returns_empty_for_unrelated_text() -> None:
    text = "a css class without UI context or visual claims does not match"
    assert find_appearance_assertions(text) == []

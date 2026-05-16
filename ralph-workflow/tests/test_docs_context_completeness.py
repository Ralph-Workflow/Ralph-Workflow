"""Regression tests for documentation context completeness.

Ensures that key repo-root and Sphinx pages contain enough local context
to stand on their own (bounded self-sufficiency proof).

Tests named pages for minimum local-context content:
- getting-started.md: first-run install/init/run/verify path
- concepts.md: runtime nouns used elsewhere
- reference.md: real operator reference index
- developer-reference.md: developer-facing internal API surface
"""

import pytest

from tests.doc_roots import PACKAGE_DOCS_SPHINX_DIR, REPO_ROOT_README

_SPHINX_PAGES = [
    "getting-started.md",
    "concepts.md",
    "reference.md",
    "developer-reference.md",
]

# Minimum content thresholds
_MIN_CONTENT_CHARS = 200
_MIN_DEFINED_NOUNS = 2
_MIN_REFERENCE_CHARS = 500


class TestRepoRootCompleteness:
    """Repo-root README must have minimum context."""

    def test_readme_has_install_instructions(self) -> None:
        """Repo-root README should have install/run instructions."""
        content = REPO_ROOT_README.read_text().lower()
        # Should mention installation
        has_install = any(phrase in content for phrase in ["install", "pip", "uv", "pipx", "clone"])
        assert has_install, "Repo-root README should have install instructions"

    def test_readme_has_verify_reference(self) -> None:
        """Repo-root README should reference verification."""
        content = REPO_ROOT_README.read_text().lower()
        assert "verify" in content or "test" in content, (
            "Repo-root README should reference verification"
        )


class TestSphinxPageCompleteness:
    """Sphinx pages must have bounded self-sufficiency."""

    @pytest.mark.parametrize("page", _SPHINX_PAGES)
    def test_page_exists(self, page: str) -> None:
        """Each Sphinx page must exist."""
        path = PACKAGE_DOCS_SPHINX_DIR / page
        assert path.exists(), f"Sphinx page {page} must exist"

    @pytest.mark.parametrize("page", _SPHINX_PAGES)
    def test_page_has_substantive_content(self, page: str) -> None:
        """Each Sphinx page must have substantive content (not just headers)."""
        path = PACKAGE_DOCS_SPHINX_DIR / page
        content = path.read_text()
        # Should have substantial content (at least 200 chars beyond frontmatter)
        assert len(content) > _MIN_CONTENT_CHARS, f"{page} appears to lack substantive content"


def test_getting_started_has_install_run_verify() -> None:
    """getting-started.md must contain install/run/verify path."""
    path = PACKAGE_DOCS_SPHINX_DIR / "getting-started.md"
    if not path.exists():
        pytest.skip("getting-started.md may not exist")
    content = path.read_text().lower()
    # Must have install, run, and verify
    assert any(phrase in content for phrase in ["install", "pip", "uv", "setup"]), (
        "getting-started.md should have install instructions"
    )
    assert "run" in content or "execute" in content, (
        "getting-started.md should have run instructions"
    )
    assert "verify" in content or "test" in content, (
        "getting-started.md should have verify instructions"
    )


def test_concepts_defines_runtime_nouns() -> None:
    """concepts.md must define runtime nouns used elsewhere."""
    path = PACKAGE_DOCS_SPHINX_DIR / "concepts.md"
    if not path.exists():
        pytest.skip("concepts.md may not exist")
    content = path.read_text().lower()
    # Should define key runtime concepts
    key_nouns = ["phase", "session", "workspace", "artifact", "handoff"]
    defined_nouns = [noun for noun in key_nouns if noun in content]
    assert len(defined_nouns) >= _MIN_DEFINED_NOUNS, (
        "concepts.md should define key runtime nouns like phase, session, workspace"
    )


def test_reference_is_operator_reference() -> None:
    """reference.md must be a real operator reference index."""
    path = PACKAGE_DOCS_SPHINX_DIR / "reference.md"
    if not path.exists():
        pytest.skip("reference.md may not exist")
    content = path.read_text()
    # Should have substantial reference content
    assert len(content) > _MIN_REFERENCE_CHARS, (
        "reference.md should be a substantial operator reference"
    )
    # Should reference CLI or commands
    content_lower = content.lower()
    has_commands = any(
        cmd in content_lower for cmd in ["ralph", "command", "cli", "flag", "option"]
    )
    assert has_commands, "reference.md should cover CLI/commands"


def test_developer_reference_points_to_internals() -> None:
    """developer-reference.md must point developers to internal surfaces."""
    path = PACKAGE_DOCS_SPHINX_DIR / "developer-reference.md"
    if not path.exists():
        pytest.skip("developer-reference.md may not exist")
    content = path.read_text()
    # Should point to internal API surfaces
    content_lower = content.lower()
    points_to_internals = any(
        indicator in content_lower for indicator in ["api", "internal", "module", "ralph."]
    )
    assert points_to_internals, (
        "developer-reference.md should point developers to internal API surfaces"
    )

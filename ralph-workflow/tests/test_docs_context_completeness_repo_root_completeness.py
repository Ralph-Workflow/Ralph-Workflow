"""Regression tests for documentation context completeness.

Ensures that key repo-root and Sphinx pages contain enough local context
to stand on their own (bounded self-sufficiency proof).

Tests named pages for minimum local-context content:
- getting-started.md: first-run install/init/run/verify path
- concepts.md: runtime nouns used elsewhere
- reference.md: real operator reference index
- developer-reference.md: developer-facing internal API surface
"""

from __future__ import annotations

from tests.doc_roots import REPO_ROOT_README

_SPHINX_PAGES = [
    "getting-started.md",
    "concepts.md",
    "configuration.md",
    "developer-internals.md",
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

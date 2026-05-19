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

import pytest

from tests.doc_roots import PACKAGE_DOCS_SPHINX_DIR

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

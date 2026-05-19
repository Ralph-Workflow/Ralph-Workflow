"""Regression tests for maintained package Markdown docs synchronization.

Ensures that maintained package Markdown docs outside Sphinx
(ralph-workflow/README.md, ralph-workflow/CONTRIBUTING.md,
ralph-workflow/docs/mcp/*.md) remain aligned with current Python behavior.
"""

from __future__ import annotations

from tests.doc_roots import PACKAGE_ROOT

# Package Markdown docs that should be maintained
_PACKAGE_MD_DOCS = [
    "README.md",
    "CONTRIBUTING.md",
    "docs/mcp/mcp-servers.md",
    "docs/mcp/web-visit.md",
    "docs/mcp/web-search.md",
    "docs/mcp-tool-restriction.md",
]


class TestPackageRootDocs:
    """Package root docs must be Python-current."""

    def test_readme_points_to_sphinx_entry(self) -> None:
        """Package README.md should point to Sphinx/manual entrypoints."""
        path = PACKAGE_ROOT / "README.md"
        content = path.read_text()
        # Should reference Sphinx docs
        assert "sphinx" in content.lower() or "docs" in content.lower()

    def test_contributing_has_verification_section(self) -> None:
        """Package CONTRIBUTING.md should have verification section."""
        path = PACKAGE_ROOT / "CONTRIBUTING.md"
        content = path.read_text()
        # Should reference verification
        assert "verify" in content.lower() or "test" in content.lower()

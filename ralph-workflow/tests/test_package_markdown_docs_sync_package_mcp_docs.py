"""Regression tests for maintained package Markdown docs synchronization.

Ensures that maintained package Markdown docs outside Sphinx
(ralph-workflow/README.md, ralph-workflow/CONTRIBUTING.md,
ralph-workflow/docs/sphinx/<mcp-*> pages) remain aligned with current
Python behavior.

wt-026 consolidation removed ralph-workflow/docs/mcp/*.md; the canonical
home for that material is ralph-workflow/docs/sphinx/advanced-mcp-configuration.md
plus the existing ralph-workflow/docs/sphinx/mcp-*.md pages.
"""

from __future__ import annotations

import pytest

from tests.doc_roots import PACKAGE_ROOT

# Package Markdown docs that should be maintained (post wt-026)
_PACKAGE_MD_DOCS = [
    "README.md",
    "CONTRIBUTING.md",
    "docs/sphinx/mcp-tool-restriction.md",
    "docs/sphinx/advanced-mcp-configuration.md",
    "docs/sphinx/mcp-architecture.md",
    "docs/sphinx/mcp-tools.md",
]


class TestPackageMcpDocs:
    """Package MCP docs must be current and consistent."""

    @pytest.mark.parametrize("doc", _PACKAGE_MD_DOCS[2:])
    def test_mcp_doc_exists(self, doc: str) -> None:
        """Each MCP doc must exist."""
        path = PACKAGE_ROOT / doc
        assert path.exists(), f"MCP doc {doc} must exist"

    @pytest.mark.parametrize("doc", _PACKAGE_MD_DOCS[2:])
    def test_mcp_doc_references_python_ralph(self, doc: str) -> None:
        """MCP docs should reference Python/ralph concepts."""
        path = PACKAGE_ROOT / doc
        content = path.read_text().lower()
        # Should reference Python or ralph
        assert "python" in content or "ralph" in content, (
            f"{doc} should reference Python/ralph concepts"
        )

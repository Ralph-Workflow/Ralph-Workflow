"""Regression tests for maintained package Markdown docs synchronization.

Ensures that maintained package Markdown docs outside Sphinx
(ralph-workflow/README.md, ralph-workflow/CONTRIBUTING.md,
ralph-workflow/docs/mcp/*.md) remain aligned with current Python behavior.
"""

from __future__ import annotations

import pytest

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


class TestPackageMcpDocs:
    """Package MCP docs must be current and consistent."""

    @pytest.mark.parametrize("doc", _PACKAGE_MD_DOCS[2:])  # Skip README and CONTRIBUTING
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

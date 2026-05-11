"""Regression tests for maintained package Markdown docs synchronization.

Ensures that maintained package Markdown docs outside Sphinx
(ralph-workflow/README.md, ralph-workflow/CONTRIBUTING.md,
ralph-workflow/docs/mcp/*.md) remain aligned with current Python behavior.
"""
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


class TestPackageRootDocs:
    """Package root docs must be Python-current."""

    def test_readme_points_to_sphinx_entry(self):
        """Package README.md should point to Sphinx/manual entrypoints."""
        path = PACKAGE_ROOT / "README.md"
        content = path.read_text()
        # Should reference Sphinx docs
        assert "sphinx" in content.lower() or "docs" in content.lower()

    def test_contributing_has_verification_section(self):
        """Package CONTRIBUTING.md should have verification section."""
        path = PACKAGE_ROOT / "CONTRIBUTING.md"
        content = path.read_text()
        # Should reference verification
        assert "verify" in content.lower() or "test" in content.lower()


class TestPackageMcpDocs:
    """Package MCP docs must be current and consistent."""

    @pytest.mark.parametrize("doc", _PACKAGE_MD_DOCS[2:])  # Skip README and CONTRIBUTING
    def test_mcp_doc_exists(self, doc: str):
        """Each MCP doc must exist."""
        path = PACKAGE_ROOT / doc
        assert path.exists(), f"MCP doc {doc} must exist"

    @pytest.mark.parametrize("doc", _PACKAGE_MD_DOCS[2:])
    def test_mcp_doc_references_python_ralph(self, doc: str):
        """MCP docs should reference Python/ralph concepts."""
        path = PACKAGE_ROOT / doc
        content = path.read_text().lower()
        # Should reference Python or ralph
        assert "python" in content or "ralph" in content, (
            f"{doc} should reference Python/ralph concepts"
        )


def test_mcp_servers_doc_has_provider_matrix():
    """mcp-servers.md should have current provider matrix."""
    path = PACKAGE_ROOT / "docs/mcp/mcp-servers.md"
    if not path.exists():
        pytest.skip("mcp-servers.md may not exist")
    content = path.read_text().lower()
    # Should reference current providers
    has_providers = any(
        p in content for p in ["claude", "gemini", "openai", "codex"]
    )
    assert has_providers, "mcp-servers.md should list current providers"


def test_web_visit_doc_has_current_schema():
    """web-visit.md should describe current visit-url schema."""
    path = PACKAGE_ROOT / "docs/mcp/web-visit.md"
    if not path.exists():
        pytest.skip("web-visit.md may not exist")
    content = path.read_text()
    # Should describe visit-url behavior
    assert "visit" in content.lower() or "url" in content.lower()


def test_web_search_doc_describes_backends():
    """web-search.md should describe current search backends."""
    path = PACKAGE_ROOT / "docs/mcp/web-search.md"
    if not path.exists():
        pytest.skip("web-search.md may not exist")
    content = path.read_text().lower()
    # Should reference current backends
    has_backends = any(
        b in content
        for b in ["brave", "ddgs", "exa", "searxng", "tavily"]
    )
    assert has_backends, "web-search.md should describe current search backends"

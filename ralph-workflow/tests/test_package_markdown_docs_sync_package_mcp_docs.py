from __future__ import annotations

import pytest

from tests.doc_roots import PACKAGE_ROOT

_PACKAGE_MD_DOCS = [
    "README.md",
    "CONTRIBUTING.md",
    "docs/sphinx/mcp-tool-restriction.md",
    "docs/sphinx/advanced-mcp-configuration.md",
    "docs/sphinx/mcp-architecture.md",
    "docs/sphinx/mcp-tools.md",
]


class TestPackageMcpDocs:
    @pytest.mark.parametrize("doc", _PACKAGE_MD_DOCS[2:])
    def test_mcp_doc_exists(self, doc: str) -> None:
        path = PACKAGE_ROOT / doc
        assert path.exists(), f"MCP doc {doc} must exist"

    @pytest.mark.parametrize("doc", _PACKAGE_MD_DOCS[2:])
    def test_mcp_doc_references_python_ralph(self, doc: str) -> None:
        path = PACKAGE_ROOT / doc
        content = path.read_text().lower()
        assert "python" in content or "ralph" in content, (
            f"{doc} should reference Python/ralph concepts"
        )

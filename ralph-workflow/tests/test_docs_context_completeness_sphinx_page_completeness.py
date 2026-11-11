from __future__ import annotations

import pytest

from tests.doc_roots import PACKAGE_DOCS_SPHINX_DIR

_SPHINX_PAGES = [
    "getting-started.md",
    "concepts.md",
    "configuration.md",
    "developer-internals.md",
]

_MIN_CONTENT_CHARS = 200
_MIN_DEFINED_NOUNS = 2
_MIN_REFERENCE_CHARS = 500


class TestSphinxPageCompleteness:
    @pytest.mark.parametrize("page", _SPHINX_PAGES)
    def test_page_exists(self, page: str) -> None:
        path = PACKAGE_DOCS_SPHINX_DIR / page
        assert path.exists(), f"Sphinx page {page} must exist"

    @pytest.mark.parametrize("page", _SPHINX_PAGES)
    def test_page_has_substantive_content(self, page: str) -> None:
        path = PACKAGE_DOCS_SPHINX_DIR / page
        content = path.read_text()
        assert len(content) > _MIN_CONTENT_CHARS, f"{page} appears to lack substantive content"

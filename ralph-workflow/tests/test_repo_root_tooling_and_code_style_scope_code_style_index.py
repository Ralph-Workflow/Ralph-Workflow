from __future__ import annotations

from tests.doc_roots import (
    REPO_ROOT_DOCS_CODE_STYLE_DIR,
)

_CODE_STYLE_INDEX = REPO_ROOT_DOCS_CODE_STYLE_DIR / "index.md"


class TestCodeStyleIndex:
    def test_index_exists(self) -> None:
        assert _CODE_STYLE_INDEX.exists(), (
            "docs/code-style/index.md must exist as the family entrypoint"
        )

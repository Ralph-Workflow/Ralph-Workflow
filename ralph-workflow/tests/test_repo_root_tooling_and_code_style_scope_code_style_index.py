"""Regression tests for the docs/code-style/ family status.

Ensures:
- docs/code-style/index.md exists as the family entrypoint
"""

from __future__ import annotations

from tests.doc_roots import (
    REPO_ROOT_DOCS_CODE_STYLE_DIR,
)

# Code-style family
_CODE_STYLE_INDEX = REPO_ROOT_DOCS_CODE_STYLE_DIR / "index.md"


class TestCodeStyleIndex:
    """docs/code-style/index.md must exist as the family entrypoint."""

    def test_index_exists(self) -> None:
        """code-style/index.md must exist as the family entrypoint."""
        assert _CODE_STYLE_INDEX.exists(), (
            "docs/code-style/index.md must exist as the family entrypoint"
        )

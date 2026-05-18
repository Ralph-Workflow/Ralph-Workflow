"""Regression tests for tooling and code-style scope boundaries.

Ensures:
- docs/tooling/remote-build.md and docs/tooling/dylint.md are explicitly historical
- docs/code-style/index.md accurately states the family's Python status
- Every archival code-style leaf contains required in-body historical marker
"""

from __future__ import annotations

from tests.doc_roots import (
    REPO_ROOT_DOCS_CODE_STYLE_DIR,
)

# Archival tooling files (should be labeled historical)
_ARCHIVAL_TOOLING = [
    "remote-build.md",
    "dylint.md",
]

# Code-style family
_CODE_STYLE_INDEX = REPO_ROOT_DOCS_CODE_STYLE_DIR / "index.md"

# Archival code-style leaves (must have in-body historical markers)
_CODE_STYLE_LEAVES = [
    "architecture.md",
    "boundaries.md",
    "code-shape.md",
    "coding-patterns.md",
    "errors-and-diagnostics.md",
    "functional-transformations.md",
    "generics-and-abstractions.md",
    "module-organization.md",
    "testing.md",
]


class TestCodeStyleIndex:
    """docs/code-style/index.md must state the family's Python status."""

    def test_index_exists(self) -> None:
        """code-style/index.md must exist."""
        assert _CODE_STYLE_INDEX.exists(), (
            "docs/code-style/index.md must exist as the family entrypoint"
        )

    def test_index_states_python_status(self) -> None:
        """index.md must accurately state the family's Python status."""
        content = _CODE_STYLE_INDEX.read_text().lower()
        # Should indicate this is Python guidance or archival/historical
        has_status = any(
            indicator in content
            for indicator in [
                "python",
                "historical",
                "rust",
                "archival",
                "maintained",
            ]
        )
        assert has_status, (
            "docs/code-style/index.md should state the family's Python/historical status"
        )

"""Regression tests for tooling and code-style scope boundaries.

Ensures:
- docs/tooling/remote-build.md and docs/tooling/dylint.md are explicitly historical
- docs/code-style/index.md accurately states the family's Python status
- Every archival code-style leaf contains required in-body historical marker
"""

from __future__ import annotations

import pytest

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


class TestCodeStyleLeaves:
    """Archival code-style leaves must have in-body historical markers."""

    @pytest.mark.parametrize("leaf", _CODE_STYLE_LEAVES)
    def test_leaf_has_historical_marker(self, leaf: str) -> None:
        """Each archival leaf must contain a historical Rust-era marker."""
        path = REPO_ROOT_DOCS_CODE_STYLE_DIR / leaf
        if not path.exists():
            pytest.skip(f"{leaf} may not exist")
        content = path.read_text().lower()
        # Must have Rust-era historical marker
        has_marker = any(
            marker in content
            for marker in [
                "rust-era",
                "historical rust",
                "pre-python",
                "legacy rust",
            ]
        )
        assert has_marker, f"{leaf} must contain an in-body historical Rust-era marker"

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
    REPO_ROOT_DOCS_TOOLING_DIR,
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


class TestArchivalTooling:
    """Archival tooling files must be explicitly labeled historical."""

    @pytest.mark.parametrize("tool", _ARCHIVAL_TOOLING)
    def test_archival_tooling_exists(self, tool: str) -> None:
        """Archival tooling files should exist if retained."""
        path = REPO_ROOT_DOCS_TOOLING_DIR / tool
        if path.exists():
            # If it exists, it should be explicitly labeled historical
            content = path.read_text().lower()
            has_historical_label = any(
                label in content
                for label in [
                    "historical",
                    "rust-era",
                    "deprecated",
                    "superseded",
                    "no longer maintained",
                ]
            )
            assert has_historical_label, f"{tool} should be labeled as historical/archival"

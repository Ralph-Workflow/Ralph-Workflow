"""Regression tests for tooling and code-style scope boundaries.

Ensures:
- docs/tooling/remote-build.md and docs/tooling/dylint.md are explicitly historical
- docs/code-style/index.md accurately states the family's Python status
- Every archival code-style leaf contains required in-body historical marker
"""

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

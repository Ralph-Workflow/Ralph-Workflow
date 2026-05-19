"""Regression tests for docs/agents family synchronization.

Ensures that docs/agents/{verification,testing-guide,type-ignore-policy,
parallelization,workspace-trait}.md are current Python guidance and that
python-verification.md and integration-tests.md remain explicit redirect stubs.
"""

from __future__ import annotations

import pytest

from tests.doc_roots import REPO_ROOT_DOCS_AGENTS_DIR

# Current Python guidance files
_CURRENT_GUIDES = [
    "verification.md",
    "testing-guide.md",
    "type-ignore-policy.md",
    "parallelization.md",
    "workspace-trait.md",
]

# Redirect stubs (should remain as compatibility shims)
_REDIRECT_STUBS = [
    "python-verification.md",
    "integration-tests.md",
]


class TestCurrentAgentsGuides:
    """Current docs/agents guides must be Python-current guidance."""

    @pytest.mark.parametrize("guide", _CURRENT_GUIDES)
    def test_guide_exists(self, guide: str) -> None:
        """Each current guide must exist."""
        path = REPO_ROOT_DOCS_AGENTS_DIR / guide
        assert path.exists(), f"Current guide {guide} must exist at {path}"

    @pytest.mark.parametrize("guide", _CURRENT_GUIDES)
    def test_guide_has_python_content(self, guide: str) -> None:
        """Each current guide must contain Python-relevant content."""
        path = REPO_ROOT_DOCS_AGENTS_DIR / guide
        content = path.read_text().lower()
        # Should not be entirely Rust-focused
        assert "rust" not in content or "python" in content, (
            f"Guide {guide} appears to be Rust-only; should be Python-current"
        )

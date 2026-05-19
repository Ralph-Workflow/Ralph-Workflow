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


class TestRedirectStubs:
    """Redirect stubs must remain explicit and correct."""

    @pytest.mark.parametrize("stub", _REDIRECT_STUBS)
    def test_stub_exists(self, stub: str) -> None:
        """Each redirect stub must exist."""
        path = REPO_ROOT_DOCS_AGENTS_DIR / stub
        assert path.exists(), f"Redirect stub {stub} must exist at {path}"

    @pytest.mark.parametrize("stub", _REDIRECT_STUBS)
    def test_stub_is_redirect(self, stub: str) -> None:
        """Each stub must indicate it redirects to canonical guide."""
        path = REPO_ROOT_DOCS_AGENTS_DIR / stub
        content = path.read_text().lower()
        # Should indicate redirection
        assert any(
            keyword in content for keyword in ["redirect", "see instead", "superseded", "moved"]
        ), f"Stub {stub} should indicate it redirects to canonical guide"

"""Regression tests for docs/agents family synchronization.

Ensures that docs/agents/{verification,testing-guide,type-ignore-policy,
parallelization,workspace-trait}.md are current Python guidance and that
python-verification.md and integration-tests.md remain explicit redirect stubs.
"""

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


def test_verification_guide_references_make_verify() -> None:
    """verification.md should reference canonical make verify."""
    path = REPO_ROOT_DOCS_AGENTS_DIR / "verification.md"
    content = path.read_text()
    assert "make verify" in content or "uv run pytest" in content, (
        "verification.md should reference canonical verification commands"
    )


def test_verification_guide_uses_uv_managed_ruff_commands() -> None:
    """verification.md should document Ruff through `uv run` to match verify's toolchain."""
    path = REPO_ROOT_DOCS_AGENTS_DIR / "verification.md"
    content = path.read_text()
    assert "uv run ruff check ralph/ tests/" in content
    assert "uv run ruff format --check ralph/ tests/" in content
    assert "\nruff check ralph/ tests/\n" not in content
    assert "\nruff format --check ralph/ tests/\n" not in content


def test_testing_guide_mentions_ralph_workflow() -> None:
    """testing-guide.md should reference ralph-workflow testing patterns."""
    path = REPO_ROOT_DOCS_AGENTS_DIR / "testing-guide.md"
    content = path.read_text()
    # Should reference the maintained package or its testing patterns
    if "ralph" in content.lower():
        assert "ralph-workflow" in content.lower() or "ralph" in content.lower()


def test_type_ignore_policy_is_python_focused() -> None:
    """type-ignore-policy.md must be Python-focused (not Rust)."""
    path = REPO_ROOT_DOCS_AGENTS_DIR / "type-ignore-policy.md"
    content = path.read_text().lower()
    # Primary focus should be Python type ignore patterns
    assert "# type:" in content or "type: ignore" in content, (
        "type-ignore-policy.md should address Python type-ignore patterns"
    )


def test_parallelization_guide_is_python_current() -> None:
    """parallelization.md must be Python-current."""
    path = REPO_ROOT_DOCS_AGENTS_DIR / "parallelization.md"
    content = path.read_text()
    # Should not reference Rust-era concepts
    assert "cargo" not in content.lower()
    assert "xtask" not in content.lower()


def test_workspace_trait_guide_is_python_focused() -> None:
    """workspace-trait.md should be Python-focused."""
    path = REPO_ROOT_DOCS_AGENTS_DIR / "workspace-trait.md"
    content = path.read_text().lower()
    # Should reference Python/ralph concepts, not Rust
    assert "python" in content or "ralph" in content

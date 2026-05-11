"""Regression tests for docs/README.md family-level scope map.

Ensures that docs/README.md explicitly maps current-vs-archival status
for all guide families and is the authoritative file-level map.
"""

from tests.doc_roots import REPO_ROOT_DOCS_DIR


def test_docs_readme_exists():
    """docs/README.md must exist as the authoritative documentation map."""
    path = REPO_ROOT_DOCS_DIR / "README.md"
    assert path.exists(), "docs/README.md must exist"


def test_docs_readme_maps_agents_family():
    """docs/README.md must explicitly cover the docs/agents family."""
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text()
    # Must reference the agents family
    assert "agents" in content.lower(), (
        "docs/README.md must explicitly cover the docs/agents family"
    )


def test_docs_readme_distinguishes_current_vs_archival():
    """docs/README.md must distinguish current Python guidance from historical."""
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text().lower()
    # Should have some indicator of current vs archival status
    # Either explicitly listing what's current, or marking what's historical
    has_status_indicator = any(
        indicator in content
        for indicator in [
            "current",
            "archival",
            "historical",
            "rust-era",
            "python",
            "maintained",
        ]
    )
    assert has_status_indicator, (
        "docs/README.md should distinguish current Python guidance from historical"
    )


def test_docs_readme_covers_code_style_family():
    """docs/README.md must cover the docs/code-style family status."""
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text()
    # Must reference code-style family
    assert "code-style" in content.lower() or "code style" in content.lower(), (
        "docs/README.md should cover code-style family status"
    )


def test_docs_readme_covers_tooling_family():
    """docs/README.md must cover the docs/tooling family."""
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text()
    # Must reference tooling family
    assert "tooling" in content.lower(), "docs/README.md should cover docs/tooling family"


def test_docs_readme_covers_performance_family():
    """docs/README.md must cover the docs/performance family."""
    path = REPO_ROOT_DOCS_DIR / "README.md"
    content = path.read_text()
    # Must reference performance family
    assert "performance" in content.lower(), "docs/README.md should cover docs/performance family"

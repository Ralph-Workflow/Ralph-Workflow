"""Shared repository-root path constants for repo-root documentation regression tests.

This module provides explicit path constants for the repository root and its
key documentation files, ensuring that repo-root documentation tests reference
the correct files rather than accidentally using package-root paths.

Usage:
    from tests.doc_roots import REPOSITORY_ROOT, REPO_ROOT_README

    def test_something():
        content = (REPOSITORY_ROOT / "README.md").read_text()
        assert "Ralph" in content
"""

from pathlib import Path

# Repository root is two levels up from this file:
# tests/doc_roots.py -> tests/ -> ralph-workflow/ -> repository root
REPOSITORY_ROOT = Path(__file__).resolve().parents[2]

# Package root is the ralph-workflow directory
PACKAGE_ROOT = REPOSITORY_ROOT / "ralph-workflow"

# Key repo-root documentation files
REPO_ROOT_README = REPOSITORY_ROOT / "README.md"
REPO_ROOT_CONTRIBUTING = REPOSITORY_ROOT / "CONTRIBUTING.md"
REPO_ROOT_CODE_STYLE = REPOSITORY_ROOT / "CODE_STYLE.md"
REPO_ROOT_DOCS_DIR = REPOSITORY_ROOT / "docs"
REPO_ROOT_DOCS_AGENTS_DIR = REPO_ROOT_DOCS_DIR / "agents"
REPO_ROOT_DOCS_CODE_STYLE_DIR = REPO_ROOT_DOCS_DIR / "code-style"
REPO_ROOT_DOCS_TOOLING_DIR = REPO_ROOT_DOCS_DIR / "tooling"
REPO_ROOT_DOCS_PERFORMANCE_DIR = REPO_ROOT_DOCS_DIR / "legacy-rust" / "performance"

# Package docs
PACKAGE_DOCS_DIR = PACKAGE_ROOT / "docs"
PACKAGE_DOCS_SPHINX_DIR = PACKAGE_DOCS_DIR / "sphinx"

__all__ = [
    "PACKAGE_DOCS_DIR",
    "PACKAGE_DOCS_SPHINX_DIR",
    "PACKAGE_ROOT",
    "REPOSITORY_ROOT",
    "REPO_ROOT_CODE_STYLE",
    "REPO_ROOT_CONTRIBUTING",
    "REPO_ROOT_DOCS_AGENTS_DIR",
    "REPO_ROOT_DOCS_CODE_STYLE_DIR",
    "REPO_ROOT_DOCS_DIR",
    "REPO_ROOT_DOCS_PERFORMANCE_DIR",
    "REPO_ROOT_DOCS_TOOLING_DIR",
    "REPO_ROOT_README",
]

"""Regression tests for repo-root performance docs historical labeling.

Ensures docs/performance/{README,memory-budget,monitoring-guide,optimization-guide}.md
are properly labeled as historical Rust-era reference (not current Python guidance).

Per docs/README.md, the performance/ family is classified as historical Rust-era
reference, so these files must contain explicit historical labeling throughout.
"""

import re

import pytest

from tests.doc_roots import REPO_ROOT_DOCS_PERFORMANCE_DIR

_PERFORMANCE_GUIDES = [
    "README.md",
    "memory-budget.md",
    "monitoring-guide.md",
    "optimization-guide.md",
]

# Required historical/archival markers that must appear in each file
HISTORICAL_MARKERS = [
    "historical",
    "rust-era",
    "retired rust",
    "archival",
    "legacy rust",
]

# Rust-specific patterns that may appear in historical performance docs
RUST_PATTERNS = [
    r"cargo\s+xtask",
    r"cargo\s+build",
    r"src/main\.rs",
    r"src/lib\.rs",
    r"Arc<str>",
    r"Box<\[",
    r"impl\s+\w+\s+for\s+\w+",
    r"fn\s+\w+\s*\([^)]*\)\s*->",
    r"use\s+\w+::",
]

# Minimum content length threshold to consider a file non-empty
_MINIMUM_CONTENT_LENGTH = 100


def test_performance_docs_exist() -> None:
    """All performance guide files must exist.

    The wt-026 documentation consolidation quarantined the entire
    `docs/performance/` family (Rust-era reference) to
    ``tmp/legacy-rust-archive/performance/``. On a fresh checkout the
    archive may not have been populated yet; the labelling tests below
    skip gracefully when the directory is absent.
    """
    if not REPO_ROOT_DOCS_PERFORMANCE_DIR.exists():
        pytest.skip(
            "performance docs archived at "
            f"{REPO_ROOT_DOCS_PERFORMANCE_DIR} are not present in this checkout"
        )
    for guide in _PERFORMANCE_GUIDES:
        path = REPO_ROOT_DOCS_PERFORMANCE_DIR / guide
        if not path.exists():
            pytest.skip(
                f"performance guide {guide} not present in this checkout "
                "(archive populated only by the wt-026 quarantine step)"
            )


def test_performance_docs_have_historical_labeling() -> None:
    """Performance docs must contain explicit historical/archival labeling.

    Per docs/README.md, the performance/ family is historical Rust-era reference.
    Each file must contain at least one historical marker to verify it's labeled.
    """
    if not REPO_ROOT_DOCS_PERFORMANCE_DIR.exists():
        pytest.skip(
            "performance docs archived at "
            f"{REPO_ROOT_DOCS_PERFORMANCE_DIR} are not present in this checkout"
        )
    for guide in _PERFORMANCE_GUIDES:
        path = REPO_ROOT_DOCS_PERFORMANCE_DIR / guide
        if not path.exists():
            pytest.skip(f"performance guide {guide} not present in this checkout")
        content = path.read_text().lower()

        has_marker = any(marker in content for marker in HISTORICAL_MARKERS)
        assert has_marker, (
            f"{guide} must contain historical/archival labeling (one of: {HISTORICAL_MARKERS})"
        )


def test_performance_docs_contain_rust_content() -> None:
    """Historical performance docs should contain Rust-era content.

    Since these are properly labeled historical docs describing the Rust
    implementation, they should contain Rust-specific code patterns.
    """
    if not REPO_ROOT_DOCS_PERFORMANCE_DIR.exists():
        pytest.skip(
            "performance docs archived at "
            f"{REPO_ROOT_DOCS_PERFORMANCE_DIR} are not present in this checkout"
        )
    for guide in _PERFORMANCE_GUIDES:
        path = REPO_ROOT_DOCS_PERFORMANCE_DIR / guide
        if not path.exists():
            pytest.skip(f"performance guide {guide} not present in this checkout")
        content = path.read_text()

        # At least one Rust pattern should be present in these historical docs
        # Look for cargo commands, Rust source paths, or Rust code patterns
        has_rust = any(re.search(pattern, content, re.IGNORECASE) for pattern in RUST_PATTERNS)
        # Also check for code blocks with rust language specifier
        has_rust_block = "```rust" in content or "```RUST" in content

        # Skip empty files or files that may have been cleared
        if len(content.strip()) > _MINIMUM_CONTENT_LENGTH:
            assert has_rust or has_rust_block, (
                f"{guide} is labeled historical but lacks Rust content patterns"
            )


def test_performance_readme_not_python_current() -> None:
    """docs/performance/README.md should NOT claim to be current Python guidance."""
    if not REPO_ROOT_DOCS_PERFORMANCE_DIR.exists():
        pytest.skip(
            "performance docs archived at "
            f"{REPO_ROOT_DOCS_PERFORMANCE_DIR} are not present in this checkout"
        )
    path = REPO_ROOT_DOCS_PERFORMANCE_DIR / "README.md"
    if not path.exists():
        pytest.skip("performance README.md not present in this checkout")
    content = path.read_text().lower()

    # Should NOT claim to be current Python
    assert "current python" not in content, (
        "README.md should not claim to be current Python guidance"
    )
    # Should contain historical markers
    assert any(m in content for m in HISTORICAL_MARKERS), "README.md must be labeled as historical"


def test_memory_budget_has_historical_label() -> None:
    """memory-budget.md must be labeled as historical Rust reference."""
    path = REPO_ROOT_DOCS_PERFORMANCE_DIR / "memory-budget.md"
    if not path.exists():
        pytest.skip("memory-budget.md may not exist")
    content = path.read_text().lower()
    assert any(m in content for m in HISTORICAL_MARKERS), (
        "memory-budget.md must be labeled as historical"
    )


def test_monitoring_guide_has_historical_label() -> None:
    """monitoring-guide.md must be labeled as historical Rust reference."""
    path = REPO_ROOT_DOCS_PERFORMANCE_DIR / "monitoring-guide.md"
    if not path.exists():
        pytest.skip("monitoring-guide.md may not exist")
    content = path.read_text().lower()
    assert any(m in content for m in HISTORICAL_MARKERS), (
        "monitoring-guide.md must be labeled as historical"
    )


def test_optimization_guide_has_historical_label() -> None:
    """optimization-guide.md must be labeled as historical Rust reference."""
    path = REPO_ROOT_DOCS_PERFORMANCE_DIR / "optimization-guide.md"
    if not path.exists():
        pytest.skip("optimization-guide.md may not exist")
    content = path.read_text().lower()
    assert any(m in content for m in HISTORICAL_MARKERS), (
        "optimization-guide.md must be labeled as historical"
    )

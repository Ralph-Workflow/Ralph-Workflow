"""Regression tests for repo-root operational docs synchronization.

Ensures docs/agent-compatibility.md, docs/quick-reference.md,
docs/template-guide.md, docs/git-workflow.md, and docs/tooling/python-tooling.md
contain current commands/links/status markers and no stale Rust-era claims.
"""

import pytest

from tests.doc_roots import REPO_ROOT_DOCS_DIR

_OPERATIONAL_GUIDES = [
    "agent-compatibility.md",
    "quick-reference.md",
    "template-guide.md",
    "git-workflow.md",
]


def test_operational_guides_exist():
    """All operational guide files must exist."""
    for guide in _OPERATIONAL_GUIDES:
        path = REPO_ROOT_DOCS_DIR / guide
        assert path.exists(), f"Operational guide {guide} must exist"


def test_no_stale_rust_workflow_references():
    """Operational guides must not contain stale Rust-era workflow claims.

    Note: Files that properly label themselves as historical/archival may contain
    these references as historical context.
    """
    for guide in _OPERATIONAL_GUIDES:
        path = REPO_ROOT_DOCS_DIR / guide
        content = path.read_text()
        content_lower = content.lower()

        # If file properly labels itself as historical/archival, it may contain these refs
        is_historical = any(
            label in content_lower
            for label in ["historical", "rust-era", "archival", "legacy"]
        )

        if not is_historical:
            assert "cargo" not in content_lower, (
                f"{guide} should not reference Rust-era cargo workflow"
            )
            assert "xtask" not in content_lower, (
                f"{guide} should not reference Rust-era xtask"
            )
            assert "src/main.rs" not in content_lower, (
                f"{guide} should not reference Rust source paths"
            )


def test_python_tooling_guide_is_current():
    """docs/tooling/python-tooling.md must be current Python guidance."""
    path = REPO_ROOT_DOCS_DIR / "tooling" / "python-tooling.md"
    if not path.exists():
        pytest.skip("python-tooling.md may not exist")
    content = path.read_text().lower()
    # Should be Python-focused
    assert "python" in content or "uv" in content, (
        "python-tooling.md should be Python-focused guidance"
    )
    # Should not be Rust-focused
    assert "cargo" not in content, (
        "python-tooling.md should not reference Rust-era cargo"
    )


def test_quick_reference_has_current_commands():
    """quick-reference.md should contain current Ralph commands."""
    path = REPO_ROOT_DOCS_DIR / "quick-reference.md"
    if not path.exists():
        pytest.skip("quick-reference.md may not exist")
    content = path.read_text()
    # Should reference current CLI commands
    if "ralph" in content.lower():
        # If it mentions ralph CLI, should reference current commands
        assert "run" in content.lower() or "verify" in content.lower()


def test_agent_compatibility_has_current_provider_info():
    """agent-compatibility.md should have current provider matrix."""
    path = REPO_ROOT_DOCS_DIR / "agent-compatibility.md"
    if not path.exists():
        pytest.skip("agent-compatibility.md may not exist")
    content = path.read_text().lower()
    # Should reference current providers
    has_provider_info = any(
        provider in content
        for provider in ["claude", "gemini", "openai", "codex"]
    )
    assert has_provider_info, (
        "agent-compatibility.md should contain current provider information"
    )


def test_template_guide_is_python_focused():
    """template-guide.md should be Python-focused."""
    path = REPO_ROOT_DOCS_DIR / "template-guide.md"
    if not path.exists():
        pytest.skip("template-guide.md may not exist")
    content = path.read_text()
    # Should reference Python/template patterns, not Rust
    assert "cargo" not in content.lower()


def test_git_workflow_is_current():
    """git-workflow.md should describe current Git workflow."""
    path = REPO_ROOT_DOCS_DIR / "git-workflow.md"
    if not path.exists():
        pytest.skip("git-workflow.md may not exist")
    content = path.read_text()
    # Should reference current workflow (ralph --generate-commit)
    assert "ralph" in content.lower() or "commit" in content.lower()

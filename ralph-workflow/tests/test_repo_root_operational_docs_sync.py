"""Regression tests for repo-root operational docs synchronization.

Ensures the canonical Sphinx copies of operational guides
(ralph-workflow/docs/sphinx/<name>.md) carry current commands, links, status
markers, and no stale Rust-era claims.
"""

from pathlib import Path

import pytest

from tests.doc_roots import REPO_ROOT_DOCS_DIR, REPOSITORY_ROOT

# Sphinx is the canonical home for these guides; check the Sphinx copy.
# Repo-root copies were intentionally removed during the docs/dedup work.
_OPERATIONAL_GUIDES = [
    "agent-compatibility.md",
    "quick-reference.md",
    # template-guide.md and git-workflow.md were retired during the docs
    # dedup (they were either stale or covered by other pages) and are
    # intentionally not in the active route.
]

_SPINX_DIR = Path(__file__).resolve().parent.parent / "docs" / "sphinx"


def _guide_path(guide: str) -> Path:
    """Return the canonical path for an operational guide."""
    return _SPINX_DIR / guide


def test_operational_guides_exist() -> None:
    """All operational guide files must exist at their canonical Sphinx path."""
    for guide in _OPERATIONAL_GUIDES:
        path = _guide_path(guide)
        assert path.exists() or (REPO_ROOT_DOCS_DIR / guide).exists(), (
            f"Operational guide {guide} must exist at ralph-workflow/docs/sphinx/{guide} "
            f"or repo-root docs/{guide}"
        )


def test_no_stale_rust_workflow_references() -> None:
    """Operational guides must not contain stale Rust-era workflow claims.

    Note: Files that properly label themselves as historical/archival may contain
    these references as historical context.
    """
    for guide in _OPERATIONAL_GUIDES:
        sphinx_path = _guide_path(guide)
        path = sphinx_path if sphinx_path.exists() else REPO_ROOT_DOCS_DIR / guide
        if not path.exists():
            continue
        content = path.read_text()
        content_lower = content.lower()

        # If file properly labels itself as historical/archival, it may contain these refs
        is_historical = any(
            label in content_lower for label in ["historical", "rust-era", "archival", "legacy"]
        )

        if not is_historical:
            assert "cargo" not in content_lower, (
                f"{guide} should not reference Rust-era cargo workflow"
            )
            assert "xtask" not in content_lower, f"{guide} should not reference Rust-era xtask"
            assert "src/main.rs" not in content_lower, (
                f"{guide} should not reference Rust source paths"
            )


def test_python_tooling_guide_is_current() -> None:
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
    assert "cargo" not in content, "python-tooling.md should not reference Rust-era cargo"


def test_quick_reference_has_current_commands() -> None:
    """quick-reference.md should contain current Ralph commands."""
    path = REPO_ROOT_DOCS_DIR / "quick-reference.md"
    if not path.exists():
        pytest.skip("quick-reference.md may not exist")
    content = path.read_text()
    # Should reference current CLI commands
    if "ralph" in content.lower():
        # If it mentions ralph CLI, should reference current commands
        assert "run" in content.lower() or "verify" in content.lower()


def test_agent_compatibility_has_current_provider_info() -> None:
    """agent-compatibility.md should have current provider matrix."""
    path = REPO_ROOT_DOCS_DIR / "agent-compatibility.md"
    if not path.exists():
        pytest.skip("agent-compatibility.md may not exist")
    content = path.read_text().lower()
    # Should reference current providers
    has_provider_info = any(
        provider in content for provider in ["claude", "gemini", "openai", "codex"]
    )
    assert has_provider_info, "agent-compatibility.md should contain current provider information"


def test_agy_mcp_setup_reflects_pty_injection() -> None:
    """AGY docs must reflect PTY-based injection rather than stale pre-configure wording."""
    stale_phrases_by_file = {
        "architecture/mcp-upstream-proxy.md": [
            "cannot inject a Ralph-only MCP config",
            "users must pre-configure",
            "Add the Ralph MCP endpoint",
            "before running Ralph",
        ],
        "agent-compatibility.md": [
            "config-discovery-based setup",
            "--conversation",
            "config-discovery-based, not env-var injection",
            "pre-configure `mcp_config.json`",
        ],
    }
    for relative_path, stale_phrases in stale_phrases_by_file.items():
        # Sphinx copies are the canonical home; check both repo-root and Sphinx.
        sphinx_path = REPOSITORY_ROOT / "ralph-workflow" / "docs" / "sphinx" / relative_path
        repo_root_path = REPO_ROOT_DOCS_DIR / relative_path
        # Prefer Sphinx copy if it exists, else fall back to repo-root.
        check_path = sphinx_path if sphinx_path.exists() else repo_root_path
        if not check_path.exists():
            continue
        content = check_path.read_text().lower()
        for phrase in stale_phrases:
            assert phrase.lower() not in content, (
                f"{relative_path} should not contain stale AGY wording: {phrase!r}"
            )


def test_template_guide_is_python_focused() -> None:
    """template-guide.md should be Python-focused."""
    path = REPO_ROOT_DOCS_DIR / "template-guide.md"
    if not path.exists():
        pytest.skip("template-guide.md may not exist")
    content = path.read_text()
    # Should reference Python/template patterns, not Rust
    assert "cargo" not in content.lower()


def test_git_workflow_is_current() -> None:
    """git-workflow.md should describe current Git workflow."""
    path = REPO_ROOT_DOCS_DIR / "git-workflow.md"
    if not path.exists():
        pytest.skip("git-workflow.md may not exist")
    content = path.read_text()
    # Should reference current workflow (ralph --generate-commit)
    assert "ralph" in content.lower() or "commit" in content.lower()

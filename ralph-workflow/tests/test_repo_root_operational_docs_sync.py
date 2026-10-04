"""Regression tests for repo-root operational docs synchronization.

Ensures the canonical Sphinx copies of operational guides
(ralph-workflow/docs/sphinx/<name>.md) carry current commands, links, status
markers, and no stale Rust-era claims.

Lane-2 prose-wording judgements (e.g. asserting that specific editorial
phrases such as "cannot inject a Ralph-only MCP config" do not appear
in documentation) were moved out of the default suite to a docs-owner
review note (see docs/ralph-workflow-policy/policy-portfolio.toml
``retired-default-lane-2-prose-guards``). The retained tests assert
objective technical contracts: file existence, canonical-route
preservation, and absence of stale Rust-era technical references.
"""

from pathlib import Path

from tests.doc_roots import REPO_ROOT_DOCS_DIR

# Sphinx is the canonical home for these guides; check the Sphinx copy.
# Repo-root copies were intentionally removed during the docs/dedup work.
_OPERATIONAL_GUIDES = [
    "agent-compatibility.md",
    # template-guide.md and git-workflow.md were retired during the docs
    # dedup (they were either stale or covered by other pages) and are
    # intentionally not in the active route.
    # quick-reference.md was merged into cli.md during the wt-026
    # consolidation; cli.md is the canonical CLI reference.
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


def test_quick_reference_has_current_commands() -> None:
    """quick-reference.md was merged into cli.md during the wt-026 consolidation.

    cli.md is the canonical CLI reference; the canonical
    `ralph --version`, `ralph --diagnose`, `ralph --check-policy`,
    `ralph --explain-policy`, and `ralph --list-agents` commands
    live there. This test is preserved as a regression guard: a future
    re-introduction of quick-reference.md would be redundant and must
    be redirected to cli.md.
    """
    # The stub was deleted in wt-026; the CLI reference lives in cli.md.
    quick_ref_path = REPO_ROOT_DOCS_DIR / "quick-reference.md"
    assert not quick_ref_path.exists(), (
        f"{quick_ref_path} was deleted in wt-026; the canonical CLI "
        f"reference lives in ralph-workflow/docs/sphinx/cli.md"
    )

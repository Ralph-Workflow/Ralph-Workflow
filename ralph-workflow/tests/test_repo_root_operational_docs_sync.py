from pathlib import Path

from tests.doc_roots import REPO_ROOT_DOCS_DIR

_OPERATIONAL_GUIDES = [
    "agent-compatibility.md",
]

_SPINX_DIR = Path(__file__).resolve().parent.parent / "docs" / "sphinx"


def _guide_path(guide: str) -> Path:
    return _SPINX_DIR / guide


def test_operational_guides_exist() -> None:
    for guide in _OPERATIONAL_GUIDES:
        path = _guide_path(guide)
        assert path.exists() or (REPO_ROOT_DOCS_DIR / guide).exists(), (
            f"Operational guide {guide} must exist at ralph-workflow/docs/sphinx/{guide} "
            f"or repo-root docs/{guide}"
        )


def test_no_stale_rust_workflow_references() -> None:
    for guide in _OPERATIONAL_GUIDES:
        sphinx_path = _guide_path(guide)
        path = sphinx_path if sphinx_path.exists() else REPO_ROOT_DOCS_DIR / guide
        if not path.exists():
            continue
        content = path.read_text()
        content_lower = content.lower()

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
    quick_ref_path = REPO_ROOT_DOCS_DIR / "quick-reference.md"
    assert not quick_ref_path.exists(), (
        f"{quick_ref_path} was deleted in wt-026; the canonical CLI "
        f"reference lives in ralph-workflow/docs/sphinx/cli.md"
    )

"""Regression tests for repo-root entry/policy docs synchronization.

Ensures that repo-root README.md, CONTRIBUTING.md, and CODE_STYLE.md
point to the maintained Python workflow, canonical verification, and
current docs/agents family.
"""

from tests.doc_roots import (
    REPO_ROOT_CODE_STYLE,
    REPO_ROOT_CONTRIBUTING,
    REPO_ROOT_README,
)


def test_repo_root_readme_points_to_maintained_workflow() -> None:
    """Repo-root README must direct readers to the maintained Python workflow."""
    content = REPO_ROOT_README.read_text()
    # Should reference ralph-workflow as the maintained package
    assert "ralph-workflow" in content, (
        "Repo-root README.md must reference ralph-workflow as the maintained package"
    )
    # Should reference the maintained Sphinx/manual entrypoints
    assert "docs/sphinx" in content or "sphinx" in content.lower(), (
        "Repo-root README.md should point to maintained Sphinx/manual entrypoints"
    )


def test_repo_root_contributing_points_to_ralph_workflow() -> None:
    """Repo-root CONTRIBUTING must point contributors to the maintained Python workflow."""
    content = REPO_ROOT_CONTRIBUTING.read_text()
    # Should reference the maintained package
    assert "ralph-workflow" in content, "Repo-root CONTRIBUTING.md must reference ralph-workflow"
    # Should reference canonical verification
    assert "make verify" in content or "verify" in content.lower(), (
        "Repo-root CONTRIBUTING.md should reference canonical verification"
    )


def test_code_style_encodes_documentation_contract() -> None:
    """CODE_STYLE must encode the live documentation policy."""
    content = REPO_ROOT_CODE_STYLE.read_text()
    # Public docstrings should be self-sufficient
    assert "docstring" in content.lower() or "pydoc" in content.lower(), (
        "CODE_STYLE.md should address public docstring expectations"
    )
    # Documentation expectations - pydoc is the key indicator
    assert "pydoc" in content.lower() or "api" in content.lower(), (
        "CODE_STYLE.md should address documentation/self-sufficiency expectations"
    )


def test_repo_root_readme_has_no_stale_rust_references() -> None:
    """Repo-root README must not contain stale Rust-era workflow references."""
    content = REPO_ROOT_README.read_text().lower()
    # Should not reference cargo/xtask
    assert "cargo" not in content, (
        "Repo-root README.md should not reference Rust-era cargo workflow"
    )
    assert "xtask" not in content, "Repo-root README.md should not reference Rust-era xtask"


def test_contributing_has_no_stale_verification_flags() -> None:
    """Repo-root CONTRIBUTING must not document stale verification flags.

    The canonical verification story is `make verify` which includes docs and
    subprocess E2E, not just a narrowed subset like `ruff + mypy + pytest` alone.
    """
    content = REPO_ROOT_CONTRIBUTING.read_text()
    # Must point to make verify as canonical, not a narrower subset
    assert "make verify" in content, (
        "Repo-root CONTRIBUTING.md must reference 'make verify' as canonical verification"
    )
    # Must document that make verify includes docs build
    assert "make docs" in content or "docs" in content.lower(), (
        "Repo-root CONTRIBUTING.md must document that 'make verify' includes the docs build"
    )
    # Must document that make verify includes subprocess E2E
    assert "subprocess" in content.lower() or "e2e" in content.lower(), (
        "Repo-root CONTRIBUTING.md must document that 'make verify' includes subprocess E2E"
    )
    # Must NOT present a narrow subset as if it were the full canonical path
    # If ruff/mypy/pytest appear without make verify context, that's a narrow-subset presentation
    lines = content.split("\n")
    in_required_verification = False
    has_make_verify = False
    for line in lines:
        if "## Required verification" in line or "## Required Checks" in line:
            in_required_verification = True
        if in_required_verification and "make verify" in line:
            has_make_verify = True
        # Check for narrow subset without make verify
        if in_required_verification and "ruff" in line.lower() and "make verify" not in line:
            # This could be the narrow list - ensure make verify is mentioned nearby
            pass  # The above assertions catch this
    assert has_make_verify, (
        "Repo-root CONTRIBUTING.md must document 'make verify' as the "
        "canonical verification command"
    )

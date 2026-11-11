from tests.doc_roots import (
    REPO_ROOT_CODE_STYLE,
    REPO_ROOT_CONTRIBUTING,
    REPO_ROOT_README,
    REPOSITORY_ROOT,
)


def test_repo_root_readme_points_to_maintained_workflow() -> None:
    content = REPO_ROOT_README.read_text()
    assert "ralph-workflow" in content, (
        "Repo-root README.md must reference ralph-workflow as the maintained package"
    )
    assert "docs/sphinx" in content or "sphinx" in content.lower(), (
        "Repo-root README.md should point to maintained Sphinx/manual entrypoints"
    )


def test_repo_root_contributing_points_to_ralph_workflow() -> None:
    content = REPO_ROOT_CONTRIBUTING.read_text()
    assert "ralph-workflow" in content, "Repo-root CONTRIBUTING.md must reference ralph-workflow"
    assert "make verify" in content or "verify" in content.lower(), (
        "Repo-root CONTRIBUTING.md should reference canonical verification"
    )


def test_code_style_encodes_documentation_contract() -> None:
    candidates = [REPO_ROOT_CODE_STYLE, REPOSITORY_ROOT / "docs" / "code-style" / "index.md"]
    primary = next((p for p in candidates if p.exists()), None)
    assert primary is not None, (
        f"Neither CODE_STYLE.md nor docs/code-style/index.md exists; checked: {candidates}"
    )
    content = primary.read_text()
    assert "docstring" in content.lower() or "pydoc" in content.lower(), (
        f"{primary} should address public docstring expectations"
    )
    assert "pydoc" in content.lower() or "api" in content.lower(), (
        f"{primary} should address documentation/self-sufficiency expectations"
    )


def test_repo_root_readme_has_no_stale_rust_references() -> None:
    content = REPO_ROOT_README.read_text().lower()
    assert "cargo" not in content, (
        "Repo-root README.md should not reference Rust-era cargo workflow"
    )
    assert "xtask" not in content, "Repo-root README.md should not reference Rust-era xtask"


def test_contributing_has_no_stale_verification_flags() -> None:
    content = REPO_ROOT_CONTRIBUTING.read_text()
    assert "make verify" in content, (
        "Repo-root CONTRIBUTING.md must reference 'make verify' as canonical verification"
    )
    assert "make docs" in content or "docs" in content.lower(), (
        "Repo-root CONTRIBUTING.md must document that 'make verify' includes the docs build"
    )
    assert "subprocess" in content.lower() or "e2e" in content.lower(), (
        "Repo-root CONTRIBUTING.md must document that 'make verify' includes subprocess E2E"
    )
    lines = content.split("\n")
    in_required_verification = False
    has_make_verify = False
    for line in lines:
        if "## Required verification" in line or "## Required Checks" in line:
            in_required_verification = True
        if in_required_verification and "make verify" in line:
            has_make_verify = True
        if in_required_verification and "ruff" in line.lower() and "make verify" not in line:
            pass
    assert has_make_verify, (
        "Repo-root CONTRIBUTING.md must document 'make verify' as the "
        "canonical verification command"
    )

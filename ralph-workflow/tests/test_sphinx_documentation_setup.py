from __future__ import annotations

import importlib
import runpy
import tomllib
from pathlib import Path

from ralph import __version__

REPO_ROOT = Path(__file__).resolve().parents[1]
CONF_PATH = REPO_ROOT / "docs" / "sphinx" / "conf.py"
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
GITIGNORE_PATH = REPO_ROOT.parent / ".gitignore"
INDEX_RST_PATH = REPO_ROOT / "docs" / "sphinx" / "index.rst"
GETTING_STARTED_PATH = REPO_ROOT / "docs" / "sphinx" / "getting-started.md"
SPHINX_DIR = REPO_ROOT / "docs" / "sphinx"
DEVELOPER_INTERNALS_PATH = SPHINX_DIR / "developer-internals.md"

# Public packages that must have non-empty docstrings (pydoc-first contract)
_PUBLIC_PACKAGES_WITH_REQUIRED_DOCSTRINGS = [
    "ralph.testing",
    "ralph.checkpoint",
    "ralph.executor",
    "ralph.platform",
    "ralph.runtime",
    "ralph.recovery",
    "ralph.prompts",
    "ralph.agents.parsers",
    "ralph.mcp.webvisit",
]

# Pages that must cross-link to getting-started.md
_PAGES_WITH_GETTING_STARTED_LINKS = [
    "cli.md",
    "concepts.md",
    "configuration.md",
    "recovery.md",
    "parallel-mode.md",
    "troubleshooting.md",
]


def _index_toctree_docnames(index_content: str) -> list[str]:
    docnames: list[str] = []
    in_toctree = False

    for line in index_content.splitlines():
        stripped = line.strip()
        if stripped == ".. toctree::":
            in_toctree = True
            continue
        if not in_toctree:
            continue
        if stripped and not line.startswith("   "):
            in_toctree = False
            continue
        if not stripped or stripped.startswith(":"):
            continue
        docnames.append(stripped)

    return docnames


def test_sphinx_conf_uses_package_version() -> None:
    namespace = runpy.run_path(str(CONF_PATH))

    assert namespace["release"] == __version__
    assert namespace["version"] == __version__


def test_root_gitignore_ignores_real_sphinx_build_tree() -> None:
    gitignore = GITIGNORE_PATH.read_text(encoding="utf-8")

    assert "ralph-workflow/docs/sphinx/_build/" in gitignore


def test_getting_started_in_index_toctree() -> None:
    """'getting-started' must appear in index.rst toctree for Sphinx navigation."""
    index_content = INDEX_RST_PATH.read_text(encoding="utf-8")
    assert "getting-started" in index_content, (
        "index.rst toctree must include 'getting-started' so the new tutorial page "
        "is wired into the Sphinx navigation"
    )


def test_getting_started_file_exists_and_has_required_content() -> None:
    """getting-started.md must exist and contain required tutorial content."""
    assert GETTING_STARTED_PATH.exists(), (
        f"docs/sphinx/getting-started.md does not exist at {GETTING_STARTED_PATH}"
    )

    content = GETTING_STARTED_PATH.read_text(encoding="utf-8")

    # Must contain the literal 'ralph --init' command
    assert "ralph --init" in content, (
        "getting-started.md must contain the literal command 'ralph --init'"
    )

    # Must contain a PROMPT.md example with '# Goal'
    assert "# Goal" in content, (
        "getting-started.md must contain a '# Goal' example for PROMPT.md"
    )

    # Must link to concepts.md
    assert "concepts.md" in content, (
        "getting-started.md must link to concepts.md"
    )

    # Must link to troubleshooting.md
    assert "troubleshooting.md" in content, (
        "getting-started.md must link to troubleshooting.md"
    )


def test_sphinx_pages_link_to_getting_started() -> None:
    """Each key Sphinx page must contain a link to getting-started.md near the top."""
    sphinx_dir = REPO_ROOT / "docs" / "sphinx"
    missing: list[str] = []

    for page in _PAGES_WITH_GETTING_STARTED_LINKS:
        path = sphinx_dir / page
        assert path.exists(), f"docs/sphinx/{page} does not exist"
        # Check within first 1000 characters for the getting-started link
        content = path.read_text(encoding="utf-8")
        if "getting-started.md" not in content[:1000]:
            missing.append(page)

    assert not missing, (
        "The following Sphinx pages are missing a 'getting-started.md' link "
        "in the first 1000 characters:\n"
        + "\n".join(f"  docs/sphinx/{p}" for p in missing)
    )


def test_docs_extra_includes_linkify_dependency_when_sphinx_enables_linkify() -> None:
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    docs_extra = pyproject["project"]["optional-dependencies"]["docs"]
    namespace = runpy.run_path(str(CONF_PATH))

    assert "linkify" in namespace["myst_enable_extensions"]
    assert "linkify-it-py>=2" in docs_extra


def test_index_rst_has_navigation_callout() -> None:
    """index.rst must have both a :doc:`getting-started` reference and a note/callout."""
    content = INDEX_RST_PATH.read_text(encoding="utf-8")

    assert ":doc:`getting-started`" in content, (
        "index.rst must contain a :doc:`getting-started` cross-reference"
    )
    assert ".. note::" in content or "New here?" in content, (
        "index.rst must contain a '.. note::' admonition or a 'New here?' callout "
        "pointing new users to the getting-started page"
    )


def test_index_toctree_entries_resolve_to_real_sphinx_pages() -> None:
    """Every index.rst toctree doc reference must point at an existing page."""
    content = INDEX_RST_PATH.read_text(encoding="utf-8")
    sphinx_dir = INDEX_RST_PATH.parent

    missing = [
        docname
        for docname in _index_toctree_docnames(content)
        if not (
            (sphinx_dir / f"{docname}.md").exists()
            or (sphinx_dir / f"{docname}.rst").exists()
        )
    ]

    assert not missing, (
        "The following index.rst toctree entries do not resolve to docs/sphinx pages:\n"
        + "\n".join(f"  {docname}" for docname in missing)
    )


def _md_toctree_docnames(content: str) -> list[str]:
    """Extract toctree docnames from a MyST Markdown file."""
    docnames: list[str] = []
    in_toctree = False
    in_directive = False

    for line in content.splitlines():
        stripped = line.strip()
        if stripped == "```{toctree}":
            in_toctree = True
            in_directive = True
            continue
        if in_toctree and stripped == "```":
            in_toctree = False
            in_directive = False
            continue
        if not in_directive:
            continue
        if not stripped or stripped.startswith(":"):
            continue
        docnames.append(stripped)

    return docnames


def test_developer_internals_toctree_entries_resolve_to_real_sphinx_pages() -> None:
    """Every toctree entry in developer-internals.md must point at an existing page."""
    content = DEVELOPER_INTERNALS_PATH.read_text(encoding="utf-8")

    missing = [
        docname
        for docname in _md_toctree_docnames(content)
        if not (
            (SPHINX_DIR / f"{docname}.md").exists()
            or (SPHINX_DIR / f"{docname}.rst").exists()
        )
    ]

    assert not missing, (
        "The following developer-internals.md toctree entries do not resolve:\n"
        + "\n".join(f"  docs/sphinx/{name}.md" for name in missing)
    )


def test_agents_page_exists() -> None:
    """docs/sphinx/agents.md must exist."""
    agents_page = SPHINX_DIR / "agents.md"
    assert agents_page.exists(), (
        "docs/sphinx/agents.md does not exist. "
        "Create it as the maintainer-facing agents architecture reference."
    )


def test_agents_page_referenced_in_developer_internals_toctree() -> None:
    """developer-internals.md toctree must include 'agents'."""
    content = DEVELOPER_INTERNALS_PATH.read_text(encoding="utf-8")
    docnames = _md_toctree_docnames(content)
    assert "agents" in docnames, (
        "developer-internals.md toctree must include 'agents' so the agents "
        "architecture page is wired into the developer navigation."
    )


def test_public_packages_have_non_empty_docstrings() -> None:
    """Every public package in _PUBLIC_PACKAGES_WITH_REQUIRED_DOCSTRINGS must have a docstring."""
    missing: list[str] = []

    for pkg_name in _PUBLIC_PACKAGES_WITH_REQUIRED_DOCSTRINGS:
        mod = importlib.import_module(pkg_name)
        if not (mod.__doc__ and mod.__doc__.strip()):
            missing.append(pkg_name)

    assert not missing, (
        "The following public packages have no docstring (pydoc-first contract):\n"
        + "\n".join(f"  {pkg}" for pkg in missing)
    )

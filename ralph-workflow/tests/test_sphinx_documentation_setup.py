from __future__ import annotations

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

# Pages that must cross-link to getting-started.md
_PAGES_WITH_GETTING_STARTED_LINKS = [
    "cli.md",
    "concepts.md",
    "configuration.md",
    "recovery.md",
    "parallel-mode.md",
    "troubleshooting.md",
]


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

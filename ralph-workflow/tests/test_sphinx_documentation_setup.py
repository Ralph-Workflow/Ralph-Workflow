from __future__ import annotations

import runpy
from pathlib import Path

from ralph import __version__

REPO_ROOT = Path(__file__).resolve().parents[1]
CONF_PATH = REPO_ROOT / "docs" / "sphinx" / "conf.py"
GITIGNORE_PATH = REPO_ROOT.parent / ".gitignore"
INDEX_RST_PATH = REPO_ROOT / "docs" / "sphinx" / "index.rst"
GETTING_STARTED_PATH = REPO_ROOT / "docs" / "sphinx" / "getting-started.md"


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

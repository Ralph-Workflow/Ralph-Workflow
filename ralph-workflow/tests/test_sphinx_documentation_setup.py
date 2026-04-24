from __future__ import annotations

import runpy
from pathlib import Path

from ralph import __version__

REPO_ROOT = Path(__file__).resolve().parents[1]
CONF_PATH = REPO_ROOT / "docs" / "sphinx" / "conf.py"
GITIGNORE_PATH = REPO_ROOT.parent / ".gitignore"


def test_sphinx_conf_uses_package_version() -> None:
    namespace = runpy.run_path(str(CONF_PATH))

    assert namespace["release"] == __version__
    assert namespace["version"] == __version__


def test_root_gitignore_ignores_real_sphinx_build_tree() -> None:
    gitignore = GITIGNORE_PATH.read_text(encoding="utf-8")

    assert "ralph-workflow/docs/sphinx/_build/" in gitignore

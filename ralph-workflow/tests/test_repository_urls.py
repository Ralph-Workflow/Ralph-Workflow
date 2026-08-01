"""Regression guards for GitHub-primary canonical repository URLs."""

from __future__ import annotations

import importlib.util
import re
import runpy
import tomllib
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

if TYPE_CHECKING:

    class ProjectUrlsModule(Protocol):
        CODEBERG_MIRROR_URL: str
        GITHUB_ISSUES_URL: str
        GITHUB_REPOSITORY_URL: str


REPO_ROOT = Path(__file__).resolve().parents[1]
WORKSPACE_ROOT = REPO_ROOT.parent
PYPROJECT_PATH = REPO_ROOT / "pyproject.toml"
SPHINX_CONF_PATH = REPO_ROOT / "docs" / "sphinx" / "conf.py"
PROJECT_URLS_MODULE_PATH = REPO_ROOT / "ralph" / "project_urls.py"

_MAINTAINED_DOC_ROOTS = [
    WORKSPACE_ROOT / "README.md",
    WORKSPACE_ROOT / "CONTRIBUTING.md",
    WORKSPACE_ROOT / "docs",
    REPO_ROOT / "README.md",
    REPO_ROOT / "CONTRIBUTING.md",
    REPO_ROOT / "docs" / "README.md",
    REPO_ROOT / "docs" / "sphinx",
]

_URL_PATTERN = re.compile(r"https://(?:codeberg\.org|github\.com)/[^)>'\s\"]+")


def _load_project_urls_module() -> ProjectUrlsModule:
    assert PROJECT_URLS_MODULE_PATH.exists(), (
        "Expected a shared `ralph/project_urls.py` module for canonical repository URLs."
    )
    spec = importlib.util.spec_from_file_location("ralph.project_urls", PROJECT_URLS_MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def test_project_urls_module_defines_canonical_repo_constants() -> None:
    module = _load_project_urls_module()

    assert getattr(module, "GITHUB_REPOSITORY_URL", "")
    assert getattr(module, "GITHUB_ISSUES_URL", "")
    assert getattr(module, "CODEBERG_MIRROR_URL", "")
    assert getattr(module, "RALPH_WORKFLOW_PRO_REPOSITORY_URL", "")


def test_pyproject_project_urls_match_shared_constants() -> None:
    module = _load_project_urls_module()
    pyproject = tomllib.loads(PYPROJECT_PATH.read_text(encoding="utf-8"))
    project_urls = pyproject["project"]["urls"]

    assert project_urls["Repository"] == module.GITHUB_REPOSITORY_URL
    assert project_urls["Issues"] == module.GITHUB_ISSUES_URL


def test_sphinx_conf_exposes_repo_url_substitutions_from_shared_constants() -> None:
    module = _load_project_urls_module()
    namespace = runpy.run_path(str(SPHINX_CONF_PATH))

    substitutions = namespace.get("myst_substitutions")
    assert isinstance(substitutions, dict), (
        "docs/sphinx/conf.py must expose MyST substitutions for repository URLs."
    )
    assert substitutions["github_repository_url"] == module.GITHUB_REPOSITORY_URL
    assert substitutions["github_issues_url"] == module.GITHUB_ISSUES_URL
    assert substitutions["codeberg_mirror_url"] == module.CODEBERG_MIRROR_URL

    rst_epilog = namespace.get("rst_epilog", "")
    assert "|github_repository_url|" in rst_epilog
    assert "|github_issues_url|" in rst_epilog
    assert "|codeberg_mirror_url|" in rst_epilog


def test_maintained_docs_only_use_canonical_repo_urls() -> None:
    module = _load_project_urls_module()
    allowed_urls = {
        module.GITHUB_REPOSITORY_URL,
        module.GITHUB_ISSUES_URL,
        module.CODEBERG_MIRROR_URL,
        f"{module.GITHUB_REPOSITORY_URL}.git",
        f"{module.CODEBERG_MIRROR_URL}.git",
        getattr(module, "RALPH_WORKFLOW_PRO_REPOSITORY_URL", ""),
    }
    mismatches: list[str] = []

    for root in _MAINTAINED_DOC_ROOTS:
        paths = (
            [root] if root.is_file() else sorted(root.rglob("*.md")) + sorted(root.rglob("*.rst"))
        )
        for path in paths:
            for url in _URL_PATTERN.findall(path.read_text(encoding="utf-8")):
                if (
                    "RalphWorkflow/Ralph-Workflow" not in url
                    and "Ralph-Workflow/Ralph-Workflow" not in url
                ):
                    continue
                pro_url = getattr(module, "RALPH_WORKFLOW_PRO_REPOSITORY_URL", "")
                is_allowed = (
                    url in allowed_urls
                    or url.startswith(f"{module.GITHUB_REPOSITORY_URL}/")
                    or url.startswith(f"{module.CODEBERG_MIRROR_URL}/")
                    or (pro_url and url.startswith(f"{pro_url}/"))
                )
                if not is_allowed:
                    mismatches.append(f"{path.relative_to(WORKSPACE_ROOT)} -> {url}")

    assert not mismatches, "Maintained docs contain repository URL drift:\n" + "\n".join(mismatches)

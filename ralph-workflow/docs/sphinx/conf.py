
from __future__ import annotations

import sys
from pathlib import Path
from typing import Protocol

sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ralph import __version__
from ralph.project_urls import (
    CODEBERG_MIRROR_URL,
    GITHUB_ISSUES_URL,
    GITHUB_REPOSITORY_URL,
)

project = "Ralph Workflow"
copyright = "2026, Ralph Workflow Contributors"
author = "Ralph Workflow Contributors"
version = __version__
release = __version__

extensions = [
    "sphinx.ext.autodoc",
    "myst_parser",
    "sphinx_copybutton",
    "sphinx_design",
]

autodoc_typehints = "description"
autodoc_type_aliases = {
    "Capability": "ralph.mcp.protocol.capability_mapping.Capability",
    "SessionLike": "ralph.mcp.protocol.startup.SessionLike",
    "PhaseEntryModel": "ralph.display.phase_lifecycle.PhaseEntryModel",
    "PhaseIterationContext": "ralph.display.phase_status.PhaseIterationContext",
    "SessionCapabilities": "ralph.prompts.types.SessionCapabilities",
}

intersphinx_mapping: dict = {}

templates_path = ["_templates"]
exclude_patterns = ["_build", "build", "Thumbs.db", ".DS_Store"]

html_theme = "ralph-docs"
html_theme_path = ["_themes"]
html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
}
html_title = "Ralph Workflow"
html_description = (
    "Ralph Workflow is the maintained Python CLI for running an autonomous coding-agent loop."
)
language = "en"
html_show_sourcelink = True
html_show_sphinx = False
html_baseurl = "https://ralphworkflow.com/docs/"
html_use_index = False
html_domain_indices = False
html_static_path = ["_static"]
html_css_files: list[str] = []
html_js_files = ["ralph-docs.js"]
pygments_style = "friendly"

myst_enable_extensions = ["colon_fence", "deflist", "linkify", "substitution"]

myst_substitutions = {
    "github_repository_url": GITHUB_REPOSITORY_URL,
    "github_issues_url": GITHUB_ISSUES_URL,
    "codeberg_mirror_url": CODEBERG_MIRROR_URL,
}

rst_epilog = f"""
.. |github_repository_url| replace:: {GITHUB_REPOSITORY_URL}
.. |github_issues_url| replace:: {GITHUB_ISSUES_URL}
.. |codeberg_mirror_url| replace:: {CODEBERG_MIRROR_URL}
"""

suppress_warnings = [
    "autodoc.import_object",
    "ref.python",
    "intersphinx",
    "myst.xref_missing",
    "toc.not_included",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

linkcheck_ignore = [
    r"http://PROMPT\.md",
    r"https://docs\.claude\.com/",
    r"http://ISSUES\.md",
    r"http://FIX_RESULT\.md",
    r"http://DEVELOPMENT_RESULT\.md",
    r"http://DEVELOPMENT_ANALYSIS_DECISION\.md",
    r"http://REVIEW_ANALYSIS_DECISION\.md",
    r"http://PLAN\.md",
    r"http://CONFIG\.md",
]


class _SphinxApp(Protocol):
    def connect(self, event: str, callback: object) -> None: ...


def _on_autodoc_process_docstring(
    app: object,
    what: object,
    name: object,
    obj: object,
    options: object,
    lines: object,
) -> None:
    sys.stdout.write(".")
    sys.stdout.flush()


def setup(app: _SphinxApp) -> None:
    app.connect("autodoc-process-docstring", _on_autodoc_process_docstring)

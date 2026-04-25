"""Sphinx configuration for Ralph Workflow documentation."""

from __future__ import annotations

import sys
from pathlib import Path

# Add the ralph-workflow package to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ralph import __version__

project = "Ralph Workflow"
copyright = "2026, Ralph Workflow Contributors"  # noqa: A001
author = "Ralph Workflow Contributors"
version = __version__
release = __version__

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "myst_parser",
    "sphinx_copybutton",
    "sphinx_design",
]

autodoc_typehints = "description"
autodoc_type_aliases = {
    "Capability": "ralph.mcp.protocol.capability_mapping.Capability",
    "SessionLike": "ralph.mcp.protocol.startup.SessionLike",
}
napoleon_google_docstring = True
napoleon_numpy_docstring = False

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "build", "Thumbs.db", ".DS_Store"]

html_theme = "furo"
html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
    "light_css_variables": {
        "color-brand-primary": "#0b6bcb",
        "color-brand-content": "#0b6bcb",
        "font-stack": "'Inter', system-ui, -apple-system, 'Segoe UI', Roboto, sans-serif",
        "font-stack--monospace": "'JetBrains Mono', 'Fira Code', ui-monospace, SFMono-Regular, Menlo, Consolas, monospace",
        "color-background-primary": "#ffffff",
        "color-background-secondary": "#f6f8fa",
        "color-foreground-primary": "#0b1320",
        "color-foreground-secondary": "#4b5563",
        "color-foreground-muted": "#6b7280",
        "color-sidebar-background": "#f6f8fa",
        "color-sidebar-background-border": "#e5e7eb",
        "color-highlight-on-target": "#fff8c5",
        "color-api-name": "#0b6bcb",
        "color-api-pre-name": "#0b6bcb",
    },
    "dark_css_variables": {
        "color-brand-primary": "#5eb1ff",
        "color-brand-content": "#5eb1ff",
        "color-background-primary": "#0b0d10",
        "color-background-secondary": "#11151a",
        "color-foreground-primary": "#e6edf3",
        "color-foreground-secondary": "#9aa4b2",
        "color-foreground-muted": "#6c7682",
        "color-sidebar-background": "#0b0d10",
        "color-sidebar-background-border": "#1a1f25",
        "color-sidebar-link-text--top-level": "#e6edf3",
        "color-highlight-on-target": "#1f2933",
        "color-api-name": "#5eb1ff",
        "color-api-pre-name": "#5eb1ff",
    },
}
html_title = "Ralph Workflow"
html_show_sourcelink = True
html_show_sphinx = False
html_baseurl = "https://ralphworkflow.com/docs/"
html_static_path = ["_static"]
html_css_files = ["custom.css"]
pygments_style = "friendly"
pygments_dark_style = "github-dark"

myst_enable_extensions = ["colon_fence", "deflist", "linkify", "substitution"]

# Suppress only unavoidable autodoc import warnings for modules that rely on
# optional extras (e.g., Pydantic forward-refs that fail without build-time extras).
suppress_warnings = ["autodoc.import_object"]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

# Linkcheck: ignore false positives produced by the MyST linkify extension
# auto-linking bare words that look like domain names (PROMPT.md → http://PROMPT.md)
# and known-redirected upstream URLs.
linkcheck_ignore = [
    r"http://PROMPT\.md",
    r"https://docs\.claude\.com/",
]

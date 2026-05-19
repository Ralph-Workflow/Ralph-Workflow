"""Sphinx configuration for Ralph Workflow documentation — ralph-docs theme."""

from __future__ import annotations

import sys
from pathlib import Path

# Add the ralph-workflow package to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

from ralph import __version__

project = "Ralph Workflow"
copyright = "2026, Ralph Workflow Contributors"
author = "Ralph Workflow Contributors"
version = __version__
release = __version__

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    # sphinx.ext.intersphinx removed — we have no live cross-doc targets and the inventory
    # fetch hangs or fails in offline/CI environments. ref.python warnings are suppressed anyway.
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
napoleon_google_docstring = True
napoleon_numpy_docstring = False

# Python inventory fetch removed — cross-references to Python stdlib are not used in these docs,
# and the network fetch would hang or fail in offline/CI environments. ref.python warnings are
# already suppressed below so there is no user-visible regression.
intersphinx_mapping: dict = {}

templates_path = ["_templates"]
exclude_patterns = ["_build", "build", "Thumbs.db", ".DS_Store"]

# First-party ralph-docs theme — standalone, no Furo dependency
html_theme = "ralph-docs"
html_theme_path = ["_themes"]
html_theme_options = {
    "sidebar_hide_name": False,
    "navigation_with_keys": True,
}
html_title = "Ralph Workflow"
language = "en"
html_show_sourcelink = True
html_show_sphinx = False
html_baseurl = "https://ralphworkflow.com/docs/"
# Keep search-engine focus on task-fit, proof, and getting-started pages instead of
# auto-generated API chrome that bloats the public sitemap without improving adoption.
html_use_index = False
html_domain_indices = False
html_static_path = ["_static"]
html_css_files: list[str] = []
html_js_files = ["ralph-docs.js"]
pygments_style = "friendly"

myst_enable_extensions = ["colon_fence", "deflist", "linkify", "substitution"]

suppress_warnings = [
    "autodoc.import_object",
    "ref.python",
    # Suppress inventory fetch failures: docs.python.org may be unreachable.
    "intersphinx",
    "myst.xref_missing",
]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

linkcheck_ignore = [
    r"http://PROMPT\.md",
    r"https://docs\.claude\.com/",
]

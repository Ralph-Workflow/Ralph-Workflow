"""Sphinx configuration for Ralph Workflow documentation."""

from __future__ import annotations

import sys
from pathlib import Path

# Add the ralph-workflow package to sys.path
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

project = "Ralph Workflow"
copyright = "2024, Ralph Workflow Contributors"  # noqa: A001
author = "Ralph Workflow Contributors"
release = "0.1.0"

extensions = [
    "sphinx.ext.autodoc",
    "sphinx.ext.napoleon",
    "sphinx.ext.viewcode",
    "sphinx.ext.intersphinx",
    "myst_parser",
]

autodoc_typehints = "description"
napoleon_google_docstring = True
napoleon_numpy_docstring = False

intersphinx_mapping = {
    "python": ("https://docs.python.org/3", None),
}

templates_path = ["_templates"]
exclude_patterns = ["_build", "build", "Thumbs.db", ".DS_Store"]

html_theme = "sphinx_rtd_theme"
html_static_path = ["_static"]

suppress_warnings = ["autodoc.import_object"]

source_suffix = {
    ".rst": "restructuredtext",
    ".md": "markdown",
}

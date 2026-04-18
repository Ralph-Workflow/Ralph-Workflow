"""Public CLI package.

This package exposes the Typer application used by the ``ralph`` console script.
For most CLI-oriented pydoc usage, start with ``ralph.cli.main``.
"""

from ralph.cli.main import app

__all__ = ["app"]

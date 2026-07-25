"""Minimal pygments stubs.

We only call two pygments symbols from the display layer:
``pygments.lexers.get_lexer_for_filename`` and
``pygments.util.ClassNotFound``. Shipping a full pygments type stub
would be a maintenance burden for a single integration point, so the
display layer pulls in a deliberately narrow stub covering only the
symbols we consume.
"""

from __future__ import annotations

from pygments.lexer import Lexer
from pygments.util import ClassNotFound

__all__ = ["ClassNotFound", "Lexer", "lexers", "util"]
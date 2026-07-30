"""Minimal pygments lexers stub.

Only the symbols used by ``ralph.display.edit_preview`` are stubbed:
``get_lexer_for_filename`` returns a :class:`pygments.lexer.Lexer`.
The full pygments project exposes hundreds of language-specific lexer
classes; we do not re-export them because the edit_preview builder only
needs the ``.name`` attribute and the ``ClassNotFound`` exception
pygments raises on an unknown extension.
"""

from __future__ import annotations

from pygments.lexer import Lexer
from pygments.util import ClassNotFound

def get_lexer_for_filename(filename: str, **options: object) -> Lexer: ...
def guess_lexer(text: str, **options: object) -> Lexer: ...
def get_lexer_by_name(alias: str, **options: object) -> Lexer: ...

__all__ = ["ClassNotFound", "get_lexer_by_name", "get_lexer_for_filename", "guess_lexer"]

from __future__ import annotations

from pygments.lexer import Lexer
from pygments.util import ClassNotFound

def get_lexer_for_filename(filename: str, **options: object) -> Lexer: ...
def guess_lexer(text: str, **options: object) -> Lexer: ...
def get_lexer_by_name(alias: str, **options: object) -> Lexer: ...

__all__ = ["ClassNotFound", "get_lexer_by_name", "get_lexer_for_filename", "guess_lexer"]

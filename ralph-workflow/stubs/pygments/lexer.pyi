"""Minimal pygments Lexer stub."""

from __future__ import annotations

from collections.abc import Iterator

from pygments.token import TokenType


class Lexer:
    name: str
    aliases: tuple[str, ...]

    def get_tokens(self, text: str) -> Iterator[tuple[TokenType, str]]: ...
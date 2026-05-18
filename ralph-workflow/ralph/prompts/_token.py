"""_Token — internal tokenizer token for template parsing."""

from __future__ import annotations


class _Token:
    def __init__(self, kind: str, value: str) -> None:
        self.kind = kind
        self.value = value


__all__ = ["_Token"]

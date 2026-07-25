"""A single oldText/newText replacement."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextEdit:
    """One first-occurrence replacement of ``old_text`` by ``new_text``."""

    old_text: str
    new_text: str

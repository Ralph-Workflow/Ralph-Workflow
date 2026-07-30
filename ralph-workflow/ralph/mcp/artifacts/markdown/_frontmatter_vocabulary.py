"""Closed frontmatter vocabulary metadata."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrontmatterVocabulary:
    """Accepted values and diagnostic identity for one consumed field."""

    values: tuple[str, ...]
    rule_id: str = "SPEC010"

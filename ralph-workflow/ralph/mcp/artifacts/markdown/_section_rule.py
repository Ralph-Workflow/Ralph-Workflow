"""Closed-grammar rules for one named section in a markdown artifact."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SectionRule:
    """Closed grammar rules for one named section.

    ``allow_body`` admits plain body lines (prose or ``Key: value``
    fields) and indented item continuation lines; ``allow_blocks``
    admits ``### [ID] Title`` sub-blocks; ``require_blocks`` demands at
    least one such block. All three default off so a section rejects
    every shape it does not explicitly opt into.
    """

    required: bool = True
    require_items: bool = False
    max_items: int | None = None
    case_sensitive_ids: bool = True
    allow_body: bool = False
    allow_blocks: bool = False
    require_blocks: bool = False


__all__ = ["SectionRule"]

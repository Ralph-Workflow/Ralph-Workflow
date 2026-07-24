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

    ``repeatable`` lets the section appear more than once (the mapper
    decides how repeats merge); ``allow_items`` overrides the default
    item admission (items are otherwise admitted exactly when
    ``allow_blocks`` is off), so a free-shape section can mix stable-ID
    list items with sub-blocks.
    """

    required: bool = True
    require_items: bool = False
    max_items: int | None = None
    case_sensitive_ids: bool = True
    allow_body: bool = False
    allow_blocks: bool = False
    require_blocks: bool = False
    repeatable: bool = False
    allow_items: bool | None = None

    @property
    def items_allowed(self) -> bool:
        """Whether stable-ID list items are an admitted shape for this section."""
        return self.allow_items if self.allow_items is not None else not self.allow_blocks


__all__ = ["SectionRule"]

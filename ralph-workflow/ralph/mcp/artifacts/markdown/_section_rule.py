
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SectionRule:

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
        return self.allow_items if self.allow_items is not None else not self.allow_blocks


__all__ = ["SectionRule"]

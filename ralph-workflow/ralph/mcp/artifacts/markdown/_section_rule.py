"""Closed-grammar rules for one named section in a markdown artifact."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SectionRule:
    """Closed grammar rules for one named section."""

    required: bool = True
    require_items: bool = False
    max_items: int | None = None
    case_sensitive_ids: bool = True


__all__ = ["SectionRule"]

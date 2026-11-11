
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FrontmatterVocabulary:

    values: tuple[str, ...]
    rule_id: str = "SPEC010"

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class _ChoiceMenuOption:
    index: int
    label: str
    selected: bool

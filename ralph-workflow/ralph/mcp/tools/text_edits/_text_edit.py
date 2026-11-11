
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class TextEdit:

    old_text: str
    new_text: str

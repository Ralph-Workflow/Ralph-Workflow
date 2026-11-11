
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class ParsedLine:

    line: int
    text: str


__all__ = ["ParsedLine"]

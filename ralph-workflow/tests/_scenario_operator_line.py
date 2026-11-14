from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class OperatorLine:
    level: str
    content: str

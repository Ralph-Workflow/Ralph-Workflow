from __future__ import annotations

from dataclasses import dataclass

__all__ = ["OperatorLine"]


@dataclass(frozen=True)
class OperatorLine:
    level: str
    content: str

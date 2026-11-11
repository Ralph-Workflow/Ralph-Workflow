
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class PolicyRoute:

    target: str
    reset_loop: bool = False


__all__ = ["PolicyRoute"]

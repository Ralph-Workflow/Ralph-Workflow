
from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class RoleBid:

    role: str
    state_changed: bool = False


__all__ = ["RoleBid"]

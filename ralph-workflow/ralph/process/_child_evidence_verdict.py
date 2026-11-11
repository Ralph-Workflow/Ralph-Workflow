
from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.process._alive_by import AliveBy


@dataclass(frozen=True)
class ChildEvidenceVerdict:

    alive_by: AliveBy | None
    deferral_allowed: bool
    all_children_terminal: bool = False


__all__ = ["ChildEvidenceVerdict"]

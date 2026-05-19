"""ChildEvidenceVerdict — unified verdict from child-liveness evidence classification."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.process._alive_by import AliveBy


@dataclass(frozen=True)
class ChildEvidenceVerdict:
    """Unified verdict from child-liveness evidence classification.

    Attributes:
        alive_by: Why child work appears alive, or None if there is no evidence.
        deferral_allowed: Whether WAITING_ON_CHILD deferral should apply.
        all_children_terminal: All Ralph-tracked children have terminated.
    """

    alive_by: AliveBy | None
    deferral_allowed: bool
    all_children_terminal: bool = False


__all__ = ["ChildEvidenceVerdict"]

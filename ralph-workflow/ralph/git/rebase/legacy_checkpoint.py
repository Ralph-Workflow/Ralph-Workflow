
from __future__ import annotations

from dataclasses import dataclass
from enum import StrEnum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.git.rebase.rebase_checkpoint import RebaseCheckpoint

__all__ = ["LegacyCheckpointInspection"]


@dataclass(frozen=True)
class _LegacyCheckpointInspection:

    status: LegacyCheckpointStatus
    checkpoint: RebaseCheckpoint | None = None
    reason: str | None = None


LegacyCheckpointInspection = _LegacyCheckpointInspection


class LegacyCheckpointStatus(StrEnum):

    ABSENT = "absent"
    TERMINAL = "terminal"
    ACTIONABLE_CONFLICT = "actionable_conflict"
    BLOCKED = "blocked"

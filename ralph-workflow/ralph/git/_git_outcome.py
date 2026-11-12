from __future__ import annotations

from typing import TYPE_CHECKING, NamedTuple

if TYPE_CHECKING:
    from ralph.git._git_outcome_kind import GitOutcomeKind

__all__ = ["GitOutcome"]


class GitOutcome(NamedTuple):
    kind: GitOutcomeKind
    cause: str | None = None

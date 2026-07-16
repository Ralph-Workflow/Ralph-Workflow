"""Rebase state model for pipeline state."""

from __future__ import annotations

from pydantic import ConfigDict

from ralph.pydantic_compat import RalphBaseModel

_FROZEN = ConfigDict(frozen=True)


class RebaseState(RalphBaseModel):
    """State for git rebase operations.

    The legacy ``pending``/``in_progress``/``completed`` fields are
    preserved so existing checkpoints deserialize cleanly. The
    auto-integration fields (``last_action``/``last_reason``/
    ``last_target``/``fast_forwarded``) are the surface the runner
    reads to thread the integration outcome into the persisted
    checkpoint, and the surface operators see in run state when a
    skip or conflict is recorded. All four new fields have defaults
    so legacy checkpoints load without modification.
    """

    model_config = _FROZEN

    pending: bool = False
    in_progress: bool = False
    completed: bool = False

    # Auto-integration outcome. ``last_action`` is the high-level
    # verb (``rebased``/``merged``/``fast_forwarded``/``skipped``/
    # ``conflict``/``recovered``); ``last_reason`` is the human-readable
    # skip / failure reason when one applies; ``last_target`` is the
    # mainline branch the integration step targeted; ``fast_forwarded``
    # records whether the fast-forward phase actually advanced the
    # target ref (False on conflict, on dirty worktree, on
    # concurrent-target-update race, etc.). See
    # ``ralph.pipeline.auto_integrate`` for the producer of these
    # values.
    last_action: str | None = None
    last_reason: str | None = None
    last_target: str | None = None
    fast_forwarded: bool = False

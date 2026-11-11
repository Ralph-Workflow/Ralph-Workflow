"""Commit-review rejection effect emitted by the pre-agent tripwire gate."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
else:
    PipelinePhase = import_module("ralph.config.enums").PipelinePhase


@dataclass(frozen=True)
class CommitReviewRejectEffect:
    """Effect to reject a pending proposal before any reviewer agent runs.

    The Stage 1 deterministic tripwires run before the reviewer agent is
    invoked. A hard tripwire (mass deletion, protected deletion) produces this
    effect instead of an :class:`~ralph.pipeline.effects.InvokeAgentEffect`,
    so no agent runs, no prompt is materialised, and the rejection reaches
    the reducer through the existing ``CommitReviewRejected`` event path.

    Attributes:
        phase: Pipeline phase whose Stage 1 gate raised the hard tripwire.
        reason: Engine-owned evidence for the rejection (paths and counts).
        ref: Proposal ref sealing the rejected state for later inspection.
        hard: Whether the rejection came from a deterministic hard tripwire.
    """

    phase: PipelinePhase
    reason: str
    ref: str
    hard: bool = True

"""Prepare-prompt pipeline effect."""

from __future__ import annotations

from dataclasses import dataclass
from importlib import import_module
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from ralph.config.enums import PipelinePhase
else:
    PipelinePhase = import_module("ralph.config.enums").PipelinePhase


@dataclass(frozen=True)
class PreparePromptEffect:
    """Effect to prepare a prompt for an agent.

    Attributes:
        phase: Current pipeline phase.
        iteration: Deprecated — kept for prompt-template rendering only; None when not applicable.
        skip_materialization: When True, advance phase without materializing the prompt.
            Used for routing-only transitions (e.g. skip_invocation phases).
    """

    phase: PipelinePhase
    iteration: int | None = None
    drain: str | None = None
    previous_phase: str | None = None
    skip_materialization: bool = False

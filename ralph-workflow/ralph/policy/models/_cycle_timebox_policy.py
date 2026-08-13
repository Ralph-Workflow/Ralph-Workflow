"""CycleTimeboxPolicy Pydantic model."""

from __future__ import annotations

import math
from typing import Self

from pydantic import Field, model_validator

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel

# Bundled default cycle deadline: 120 minutes (7,200 seconds).
DEFAULT_CYCLE_TIMEBOX_SECONDS: float = 7200.0
# The soft-warning point is derived as 80% of the configured duration; it is
# never stored as an independent second duration so a custom deadline keeps the
# same warning ratio without runtime code changes.
WARNING_THRESHOLD_RATIO: float = 0.8


class CycleTimeboxPolicy(_FrozenPolicyModel):
    """Transition-bounded wall-clock timebox for a plan-to-final-commit cycle.

    Declares a generic cycle deadline that starts on the configured
    ``start_source`` -> ``start_entry`` transition (while a cycle is inactive),
    softly warns development agents once 80% of the duration has elapsed, and
    redirects expired re-entries through the finalization target so the
    workflow enters its existing final-commit path instead of starting another
    development invocation.

    The start transition is identified by its source AND target so an
    unrelated route into the same phase cannot start or reset a cycle. The
    warning point is derived as ``WARNING_THRESHOLD_RATIO`` of
    ``duration_seconds`` and is never stored as a second independent duration,
    so a custom deadline retains the same 80% behavior without code changes.
    """

    duration_seconds: float = Field(
        default=DEFAULT_CYCLE_TIMEBOX_SECONDS,
        description=(
            "Cycle deadline in seconds. Must be finite and greater than zero. "
            "The bundled default is 7200 (120 minutes)."
        ),
    )
    start_source: str = Field(
        description=(
            "Source phase of the transition that starts the timer. The timer "
            "starts only when routing advances from start_source to start_entry "
            "while the cycle is inactive. Must reference a declared phase, and "
            "the start_source -> start_entry edge must be declared in the graph."
        ),
    )
    start_entry: str = Field(
        description=(
            "Target phase of the transition that starts the timer (the phase "
            "whose entry begins the cycle). Must reference a declared phase "
            "or terminal."
        ),
    )
    guarded_entry: str = Field(
        description=(
            "Phase whose entries are guarded by the deadline. Every entry to this "
            "phase while the cycle is active is checked against the deadline. "
            "Must reference a declared phase."
        ),
    )
    end_entry: str = Field(
        description=(
            "Phase whose entry ends cycle timing (the final-commit path entry). "
            "Must reference a declared phase or terminal."
        ),
    )
    finalization_target: str = Field(
        description=(
            "Phase selected when the deadline has expired, redirecting the guarded "
            "entry through normal finalization. Must reference a declared phase or terminal."
        ),
    )

    @model_validator(mode="after")
    def _duration_finite_positive(self) -> Self:
        if not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError(
                "cycle_timebox.duration_seconds must be finite and greater than zero; "
                f"got {self.duration_seconds!r}. Use a positive finite number of seconds."
            )
        return self

    @property
    def warning_threshold_seconds(self) -> float:
        """Return the elapsed-seconds point at which the soft warning begins."""
        return self.duration_seconds * WARNING_THRESHOLD_RATIO

    @property
    def remaining_at_warning_seconds(self) -> float:
        """Return the seconds remaining once the warning begins."""
        return self.duration_seconds - self.warning_threshold_seconds

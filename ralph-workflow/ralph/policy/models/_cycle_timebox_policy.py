
from __future__ import annotations

import math
from typing import Literal, Self

from pydantic import ConfigDict, Field, model_validator

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel

DEFAULT_CYCLE_TIMEBOX_SECONDS: float = 36000.0
WARNING_THRESHOLD_RATIO: float = 0.8
DEFAULT_FINALIZATION_CYCLE_OUTCOME: Literal["completed", "failed"] = "completed"


class CycleTimeboxPolicy(_FrozenPolicyModel):

    model_config = ConfigDict(extra="forbid")

    duration_seconds: float = Field(
        default=DEFAULT_CYCLE_TIMEBOX_SECONDS,
        description=(
            "Cycle deadline in seconds. Must be finite and greater than zero. "
            "The bundled default is 36000 (600 minutes)."
        ),
    )
    start_source: str = Field(
        description=(
            "Source phase of the transition that starts the timer. The timer "
            "starts only when routing advances from start_source to start_entry "
            "while the cycle is inactive. Must reference a declared phase or "
            "terminal, and the start_source -> start_entry edge must be "
            "declared in the graph."
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
            "Must reference a declared phase or terminal."
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
    finalization_cycle_outcome: Literal["completed", "failed"] = Field(
        default=DEFAULT_FINALIZATION_CYCLE_OUTCOME,
        description=(
            "Cycle outcome stamped on a deadline redirect so post-commit routing "
            "treats the timed-out cycle like any other finished cycle: the next "
            "cycle starts while its budget counter has room. Closed to the same "
            "vocabulary post_commit_routes match on, so a value that names no "
            "route cannot silently end a run early. Never overrides an outcome "
            "the cycle already recorded. The bundled default is 'completed'."
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
        return self.duration_seconds * WARNING_THRESHOLD_RATIO

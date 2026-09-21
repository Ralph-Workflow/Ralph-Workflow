"""Development-phase timebox policy."""

from __future__ import annotations

import math
from typing import Self

from pydantic import ConfigDict, Field, model_validator

from ralph.policy.models._frozen_policy_model import _FrozenPolicyModel

DEFAULT_DEVELOPMENT_TIMEBOX_SECONDS: float = 5400.0
DEFAULT_DEVELOPMENT_WARNING_SECONDS: float = 4200.0


class DevelopmentTimeboxPolicy(_FrozenPolicyModel):
    """Independent hard-stop timer for uninterrupted development work."""

    model_config = ConfigDict(extra="forbid")

    duration_seconds: float = Field(default=DEFAULT_DEVELOPMENT_TIMEBOX_SECONDS)
    warning_seconds: float = Field(default=DEFAULT_DEVELOPMENT_WARNING_SECONDS)
    start_source: str
    start_entry: str
    guarded_entry: str
    end_entry: str
    finalization_target: str

    @model_validator(mode="after")
    def _durations_are_valid(self) -> Self:
        if not math.isfinite(self.duration_seconds) or self.duration_seconds <= 0:
            raise ValueError(
                "development_timebox.duration_seconds must be finite and greater than zero"
            )
        if not math.isfinite(self.warning_seconds) or self.warning_seconds < 0:
            raise ValueError(
                "development_timebox.warning_seconds must be finite and non-negative"
            )
        if self.warning_seconds >= self.duration_seconds:
            raise ValueError(
                "development_timebox.warning_seconds must be less than duration_seconds"
            )
        return self

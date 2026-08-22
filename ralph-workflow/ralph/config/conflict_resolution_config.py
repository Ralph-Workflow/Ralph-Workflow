"""Typed configuration for conflict-resolution supervision and routing."""

from __future__ import annotations

import math

from pydantic import ConfigDict, Field, field_validator

from ralph.pydantic_compat import RalphBaseModel


class ConflictResolutionConfig(RalphBaseModel):
    """``[conflict_resolution]`` settings.

    The resolver is supervised by one fixed inactivity window.  The optional
    total cap is deliberately different: it is an explicit operator override
    that can stop active work and is therefore reported as an operator cap,
    never as an idle timeout.
    """

    model_config = ConfigDict(frozen=True)

    inactivity_timeout_seconds: float = Field(default=900.0, gt=0.0)
    status_interval_seconds: float = Field(default=30.0, gt=0.0)
    max_rounds_per_stop: int = Field(default=3, ge=1)
    max_rebase_conflict_stops: int = Field(default=10, ge=1)
    max_fallback_agents: int = Field(default=2, ge=1)
    total_resolution_cap_seconds: float | None = Field(
        default=None,
        description=(
            "Optional absolute cap for one complete conflict resolution. Disabled"
            " by default. When set, it may stop an active resolver and is reported"
            " as OPERATOR_CAP_REACHED rather than as an idle timeout."
        ),
    )

    @field_validator(
        "inactivity_timeout_seconds", "status_interval_seconds", "total_resolution_cap_seconds"
    )
    @classmethod
    def _validate_finite_duration(cls, value: float | None) -> float | None:
        if value is not None and not math.isfinite(value):
            raise ValueError("must be finite")
        return value

    @field_validator("total_resolution_cap_seconds")
    @classmethod
    def _validate_operator_cap(cls, value: float | None) -> float | None:
        if value is not None and value <= 0.0:
            raise ValueError("must be positive when set")
        return value


__all__ = ["ConflictResolutionConfig"]

"""Testability sub-section for the plan design schema."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel

type ForbiddenInTest = str
type TestLayer = str


class Testability(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    must_be_black_box: bool = Field(
        ...,
        description="Whether tests must be black-box (no production monkeypatches).",
    )
    forbidden_in_tests: list[ForbiddenInTest] = Field(
        default_factory=list,
        max_length=50,
        description="Free-form forbidden-test-pattern hints (max 50).",
    )
    required_test_layers: list[TestLayer] = Field(
        default_factory=list,
        max_length=20,
        description="Free-form required test-layer hints (max 20).",
    )
    clock_injection_required: bool | None = Field(
        default=None,
        description="Whether a clock-injection seam is required.",
    )
    max_unit_test_seconds: float | None = Field(
        default=None,
        gt=0,
        le=60,
        description="Optional per-unit-test budget in seconds (0 < value <= 60).",
    )


__all__ = [
    "ForbiddenInTest",
    "TestLayer",
    "Testability",
]

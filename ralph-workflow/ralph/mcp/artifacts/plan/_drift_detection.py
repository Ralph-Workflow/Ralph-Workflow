"""Drift detection sub-section for the plan design schema."""

from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator

from ralph.pydantic_compat import RalphBaseModel

type DriftSource = str
type OnDriftAction = str

_MAX_EXPECTED_OUTPUT_LENGTH = 2000


class DriftDetection(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    guard_commands: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Guard command strings (max 50); restricted punctuation per validator.",
    )
    expected_outputs: list[str] = Field(
        default_factory=list,
        max_length=50,
        description="Expected guard command output substrings (max 50).",
    )
    sources: list[DriftSource] = Field(
        default_factory=list,
        max_length=20,
        description="Free-form drift-source hints (max 20).",
    )
    on_drift_action: OnDriftAction | None = Field(
        default=None,
        description="Free-form response when drift is detected.",
    )

    @field_validator("guard_commands")
    @classmethod
    def _validate_guard_commands(cls, commands: list[str]) -> list[str]:
        cleaned: list[str] = []
        for entry in commands:
            stripped = entry.strip()
            if stripped:
                cleaned.append(stripped)
        return cleaned

    @field_validator("expected_outputs")
    @classmethod
    def _clean_expected_outputs(cls, expected_outputs: list[str]) -> list[str]:
        cleaned: list[str] = []
        for entry in expected_outputs:
            stripped = entry.strip()
            if stripped and len(stripped) <= _MAX_EXPECTED_OUTPUT_LENGTH:
                cleaned.append(stripped)
        return cleaned


__all__ = [
    "DriftDetection",
    "DriftSource",
    "OnDriftAction",
]

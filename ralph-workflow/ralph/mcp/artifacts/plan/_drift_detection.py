"""Drift detection sub-section for the plan design schema."""

from __future__ import annotations

import re
from typing import Literal

from pydantic import ConfigDict, Field, field_validator

from ralph.pydantic_compat import RalphBaseModel

DriftSource = Literal[
    "ruff",
    "mypy",
    "pytest",
    "make",
    "custom-script",
    "ci",
    "unknown",
]

OnDriftAction = Literal["fail-verify", "log-only", "open-issue", "ignore"]

_SAFE_COMMAND_REGEX = re.compile(r"^[A-Za-z0-9 _./\-:=+]+$")


class DriftDetection(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    guard_commands: list[str] = Field(
        default_factory=list,
        description="Guard command strings; restricted punctuation per validator.",
    )
    expected_outputs: list[str] = Field(
        default_factory=list,
        description="Expected guard command output substrings.",
    )
    sources: list[DriftSource] = Field(
        default_factory=list,
        description="DriftSource enum list; see DriftSource literal.",
    )
    on_drift_action: OnDriftAction | None = Field(
        default=None,
        description="OnDriftAction enum; see OnDriftAction literal.",
    )

    @field_validator("guard_commands")
    @classmethod
    def _validate_guard_commands(cls, commands: list[str]) -> list[str]:
        cleaned: list[str] = []
        for entry in commands:
            stripped = entry.strip()
            if not stripped or not _SAFE_COMMAND_REGEX.match(stripped):
                msg = (
                    "drift_detection.guard_commands entries must use only "
                    "letters, digits, spaces, and these punctuation marks: "
                    "._/-:=+"
                )
                raise ValueError(msg)
            cleaned.append(stripped)
        return cleaned

    @field_validator("expected_outputs")
    @classmethod
    def _clean_expected_outputs(cls, expected_outputs: list[str]) -> list[str]:
        cleaned: list[str] = []
        for entry in expected_outputs:
            stripped = entry.strip()
            if stripped:
                cleaned.append(stripped)
        return cleaned


__all__ = [
    "DriftDetection",
    "DriftSource",
    "OnDriftAction",
]

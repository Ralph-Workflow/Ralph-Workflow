"""Canonical plan identity and edit-operation modes."""

from typing import Literal

PLAN_ARTIFACT_TYPE = "plan"
PLAN_ARTIFACT_PATH = ".agent/artifacts/plan.md"
SectionMode = Literal["replace", "append"]

__all__ = ["PLAN_ARTIFACT_PATH", "PLAN_ARTIFACT_TYPE", "SectionMode"]

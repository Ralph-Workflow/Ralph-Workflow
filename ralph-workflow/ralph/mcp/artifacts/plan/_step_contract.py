"""Shared step-type helpers for the canonical plan model."""

from __future__ import annotations

from enum import StrEnum


class StepType(StrEnum):
    """Built-in step kinds with model-level requirements."""

    FILE_CHANGE = "file_change"
    ACTION = "action"
    RESEARCH = "research"
    VERIFY = "verify"


def requires_targets(step_type: StepType | str) -> bool:
    """Return True when the step type binds to the ``targets`` contract."""
    return step_type == StepType.FILE_CHANGE


def requires_verify_handle(step_type: StepType | str) -> bool:
    """Return True when the step type binds to the verify handle contract."""
    return step_type == StepType.VERIFY


__all__ = [
    "StepType",
    "requires_targets",
    "requires_verify_handle",
]

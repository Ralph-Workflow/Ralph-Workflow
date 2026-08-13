"""Structured explanation for the plan-to-final-commit cycle timebox policy."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass
class CycleTimeboxExplanation:
    """Human-readable summary of the cycle timebox configuration.

    Mirrors the fields of
    :class:`ralph.policy.models.CycleTimeboxPolicy` plus the derived
    80% soft-warning threshold so operators can reason about the
    deadline from a single explanation view.
    """

    duration_seconds: float
    warning_threshold_seconds: float
    start_entry: str
    guarded_entry: str
    end_entry: str
    finalization_target: str

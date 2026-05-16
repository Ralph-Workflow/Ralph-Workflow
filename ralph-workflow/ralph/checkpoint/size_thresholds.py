"""Threshold values for checkpoint size checks."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class SizeThresholds:
    """Warning and error thresholds in bytes."""

    warn_threshold: int = 1_572_864
    error_threshold: int = 2_097_152

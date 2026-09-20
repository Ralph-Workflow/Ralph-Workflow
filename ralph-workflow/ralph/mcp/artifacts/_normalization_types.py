"""Shared value objects for commit-message normalization."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

Confidence = Literal["high", "medium", "low"]


@dataclass(frozen=True)
class NormalizationTransformation:
    """One auditable normalization action and its supporting evidence."""

    action: str
    source: str
    confidence: Confidence


__all__ = ["Confidence", "NormalizationTransformation"]

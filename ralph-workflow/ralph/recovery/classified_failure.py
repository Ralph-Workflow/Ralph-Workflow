"""Structured classified failure model."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .failure_category import FailureCategory


@dataclass(frozen=True)
class ClassifiedFailure:
    """A failure with its category, attribution, and budget-counting decision."""

    category: FailureCategory
    reason: str
    attributed_agent: str | None
    attributed_phase: str
    counts_against_budget: bool
    original_exception: BaseException | None
    raw_message: str
    reset_session: bool = field(default=False)

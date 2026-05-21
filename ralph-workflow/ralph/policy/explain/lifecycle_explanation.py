"""Lifecycle completion explanation structures."""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class LifecycleExplanation:
    """Human-readable lifecycle completion metadata."""

    lifecycle_name: str
    completion_phase: str
    completion_block: str
    increments_counter: str | None = None
    before_complete: list[str] = field(default_factory=list)
    after_complete: list[str] = field(default_factory=list)

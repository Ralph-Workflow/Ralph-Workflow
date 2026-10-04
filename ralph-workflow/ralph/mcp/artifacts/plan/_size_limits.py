"""The sole plan-artifact size boundary: raw UTF-8 bytes."""

from __future__ import annotations

from dataclasses import dataclass
from typing import ClassVar

_MAX_TOTAL_BYTES = 4_000_000


@dataclass(frozen=True)
class PlanSizeLimits:
    """Size limit for raw plan markdown, deliberately without shape caps."""

    max_total_bytes: int = _MAX_TOTAL_BYTES
    DEFAULT: ClassVar[PlanSizeLimits]

    def __post_init__(self) -> None:
        if self.max_total_bytes != _MAX_TOTAL_BYTES:
            raise RuntimeError("PlanSizeLimits.max_total_bytes must be 4000000")


@dataclass(frozen=True)
class PlanArtifactSizeError(ValueError):
    """A pure-return raw-byte limit violation."""

    field: str
    actual: int
    cap: int

    def __str__(self) -> str:
        return f"plan size violation: field={self.field!r} actual={self.actual} cap={self.cap}"


PlanSizeLimits.DEFAULT = PlanSizeLimits()
PLAN_SIZE_LIMITS: type[PlanSizeLimits] = PlanSizeLimits


def check_plan_size(
    content: object,
    *,
    limits: PlanSizeLimits = PlanSizeLimits.DEFAULT,
) -> PlanArtifactSizeError | None:
    """Check only a precomputed raw UTF-8 byte count, never payload shape."""
    if not isinstance(content, dict):
        return None
    raw_bytes = content.get("_raw_bytes")
    if not isinstance(raw_bytes, int):
        return None
    if raw_bytes > limits.max_total_bytes:
        return PlanArtifactSizeError("total_bytes", raw_bytes, limits.max_total_bytes)
    return None


__all__ = ["PLAN_SIZE_LIMITS", "PlanArtifactSizeError", "PlanSizeLimits", "check_plan_size"]

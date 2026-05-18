"""GuidelineSource — structural protocol for guideline objects."""

from __future__ import annotations

from typing import TYPE_CHECKING, Protocol, runtime_checkable

if TYPE_CHECKING:
    from collections.abc import Sequence


@runtime_checkable
class GuidelineSource(Protocol):
    """Structural protocol for guideline objects produced by handlers."""

    quality_checks: Sequence[str]
    security_checks: Sequence[str]
    performance_checks: Sequence[str]
    testing_checks: Sequence[str]
    documentation_checks: Sequence[str]
    idioms: Sequence[str]
    anti_patterns: Sequence[str]
    concurrency_checks: Sequence[str]
    resource_checks: Sequence[str]
    observability_checks: Sequence[str]
    secrets_checks: Sequence[str]
    api_design_checks: Sequence[str]


__all__ = ["GuidelineSource"]

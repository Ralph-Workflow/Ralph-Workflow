"""Optional extraction helpers, never a second plan acceptance gate."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.plan.plan_artifact_validation_error import PlanArtifactValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.artifacts.plan._section_models import PlanArtifactDict


def is_noop_plan(artifact: Mapping[str, object]) -> bool:
    """Only an explicit true marker is a no-op; prose plans remain active."""
    return artifact.get("noop") is True


def normalize_plan_artifact_content(content: PlanArtifactDict) -> PlanArtifactDict:
    """Preserve optional context; sanity belongs to raw Markdown."""
    return dict(content)


__all__ = [
    "PlanArtifactValidationError",
    "is_noop_plan",
    "normalize_plan_artifact_content",
]

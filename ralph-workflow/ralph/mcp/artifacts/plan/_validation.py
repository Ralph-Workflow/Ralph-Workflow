from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.artifacts.plan._section_models import PlanArtifactDict


def is_noop_plan(artifact: Mapping[str, object]) -> bool:
    return artifact.get("noop") is True


def normalize_plan_artifact_content(content: PlanArtifactDict) -> PlanArtifactDict:
    return dict(content)


__all__ = [
    "is_noop_plan",
    "normalize_plan_artifact_content",
]

"""Section registry for plan-artifact validation and merging.

The registry maps plan-section names to their Pydantic sub-models. The
section lists are a single source of truth that ``validate_plan_section``
and ``merge_plan_section`` consult instead of hard-coding the set of
valid section names in each helper. ``SectionMode`` is the closed
``replace``/``append`` enum the MCP tool uses to direct per-section
merging.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Literal

from ralph.mcp.artifacts.plan._section_models import (
    CriticalFiles,
    DesignSection,
    ParallelPlanItem,
    PlanConstraints,
    PlanStep,
    RiskMitigation,
    SkillsMcp,
    Summary,
    VerificationStep,
)

if TYPE_CHECKING:
    from ralph.pydantic_compat import RalphBaseModel

PLAN_ARTIFACT_TYPE = "plan"
PLAN_ARTIFACT_PATH = ".agent/artifacts/plan.md"
PLAN_MARKDOWN_PATH = ".agent/PLAN.md"
PLAN_DRAFT_PATH = ".agent/artifacts/.plan_draft.json"
PLAN_DRAFT_SCHEMA_VERSION = 1

SectionMode = Literal["replace", "append"]

PLAN_SECTION_OBJECT_MODELS: dict[str, type[RalphBaseModel]] = {
    "summary": Summary,
    "skills_mcp": SkillsMcp,
    "critical_files": CriticalFiles,
    "constraints": PlanConstraints,
    "design": DesignSection,
}

PLAN_SECTION_LIST_ITEM_MODELS: dict[str, type[RalphBaseModel]] = {
    "steps": PlanStep,
    "risks_mitigations": RiskMitigation,
    "verification_strategy": VerificationStep,
    "parallel_plan": ParallelPlanItem,
}

PLAN_SECTION_NAMES: frozenset[str] = frozenset(
    set(PLAN_SECTION_OBJECT_MODELS) | set(PLAN_SECTION_LIST_ITEM_MODELS) | {"work_units"}
)

__all__ = [
    "PLAN_ARTIFACT_PATH",
    "PLAN_ARTIFACT_TYPE",
    "PLAN_DRAFT_PATH",
    "PLAN_DRAFT_SCHEMA_VERSION",
    "PLAN_MARKDOWN_PATH",
    "PLAN_SECTION_LIST_ITEM_MODELS",
    "PLAN_SECTION_NAMES",
    "PLAN_SECTION_OBJECT_MODELS",
    "SectionMode",
]

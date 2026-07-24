"""Structured planning artifact validation helpers.

Module family (reading order):
  ``_section_models`` -> ``_section_registry`` -> ``_validation`` ->
  ``_noop``.
"""

# ruff: noqa: I001 RUF022  (compact import + __all__ layout keeps this file under 60 lines)
from __future__ import annotations
from ralph.mcp.artifacts.plan._noop import PlanArtifactValidationError, is_noop_plan
from ralph.mcp.artifacts.plan._plan_step import PlanStep
from ralph.mcp.artifacts.plan._size_limits import (
    PLAN_SIZE_LIMITS,
    PlanArtifactSizeError,
    PlanSizeLimits,
    check_plan_size,
)
from ralph.mcp.artifacts.plan._section_models import (
    AcceptanceCriteria,
    AcceptanceCriterion,
    CoverageArea,
    CriticalFiles,
    CriticalPrimaryFile,
    DesignSection,
    EditArea,
    EvidenceRef,
    ExpectedEvidence,
    ParallelPlanItem,
    PlanArtifactDict,
    PlanConstraints,
    PlanningProfile,
    ReferenceFile,
    RiskMitigation,
    ScopeCategory,
    ScopeItem,
    SkillsMcp,
    StepTarget,
    Summary,
    VerificationStep,
)
from ralph.mcp.artifacts.plan._section_registry import (
    PLAN_ARTIFACT_PATH,
    PLAN_ARTIFACT_TYPE,
    PLAN_SECTION_LIST_ITEM_MODELS,
    PLAN_SECTION_NAMES,
    PLAN_SECTION_OBJECT_MODELS,
    SectionMode,
)
from ralph.mcp.artifacts.plan._step_contract import (
    StepType,
    requires_targets,
    requires_verify_handle,
)
from ralph.mcp.artifacts.plan._validation import (
    PlanArtifact,
    normalize_plan_artifact_content,
    validate_plan_section,
)

__all__ = [
    "AcceptanceCriteria",
    "AcceptanceCriterion",
    "CoverageArea",
    "CriticalFiles",
    "CriticalPrimaryFile",
    "DesignSection",
    "EditArea",
    "EvidenceRef",
    "ExpectedEvidence",
    "ParallelPlanItem",
    "PLAN_ARTIFACT_PATH",
    "PLAN_ARTIFACT_TYPE",
    "PLAN_SECTION_LIST_ITEM_MODELS",
    "PLAN_SECTION_NAMES",
    "PLAN_SECTION_OBJECT_MODELS",
    "PLAN_SIZE_LIMITS",
    "PlanArtifact",
    "PlanArtifactDict",
    "PlanArtifactSizeError",
    "PlanArtifactValidationError",
    "PlanConstraints",
    "PlanSizeLimits",
    "PlanStep",
    "PlanningProfile",
    "ReferenceFile",
    "RiskMitigation",
    "ScopeCategory",
    "ScopeItem",
    "SectionMode",
    "SkillsMcp",
    "StepTarget",
    "StepType",
    "Summary",
    "VerificationStep",
    "check_plan_size",
    "is_noop_plan",
    "normalize_plan_artifact_content",
    "requires_targets",
    "requires_verify_handle",
    "validate_plan_section",
]

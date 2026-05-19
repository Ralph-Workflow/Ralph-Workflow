"""Structured Pydantic schema models for plan artifacts."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.mcp.artifacts.plan._critical_files import CriticalFiles
from ralph.mcp.artifacts.plan._critical_primary_file import CriticalPrimaryFile
from ralph.mcp.artifacts.plan._edit_area import EditArea
from ralph.mcp.artifacts.plan._plan_step import PlanStep
from ralph.mcp.artifacts.plan._reference_file import ReferenceFile
from ralph.mcp.artifacts.plan._risk_mitigation import RiskMitigation
from ralph.mcp.artifacts.plan._scope_item import ScopeItem
from ralph.mcp.artifacts.plan._skills_mcp import SkillsMcp
from ralph.mcp.artifacts.plan._step_target import StepTarget
from ralph.mcp.artifacts.plan._summary import Summary
from ralph.mcp.artifacts.plan._verification_step import VerificationStep
from ralph.pydantic_compat import RalphBaseModel


class ParallelPlanItem(RalphBaseModel):
    """A unit of parallelisable work with dependency tracking."""

    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    edit_area: EditArea
    depends_on: list[str] = Field(default_factory=list)


__all__ = [
    "CriticalFiles",
    "CriticalPrimaryFile",
    "EditArea",
    "ParallelPlanItem",
    "PlanStep",
    "ReferenceFile",
    "RiskMitigation",
    "ScopeItem",
    "SkillsMcp",
    "StepTarget",
    "Summary",
    "VerificationStep",
]

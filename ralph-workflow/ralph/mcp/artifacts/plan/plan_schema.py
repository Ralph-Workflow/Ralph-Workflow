"""Structured Pydantic schema models for plan artifacts."""

from __future__ import annotations

from typing import Literal

from pydantic import ConfigDict, Field

from ralph.pydantic_compat import RalphBaseModel


class ParallelPlanItem(RalphBaseModel):
    """A unit of parallelisable work with dependency tracking."""

    class ScopeItem(RalphBaseModel):
        """A single item describing a unit of work within the plan scope."""

        model_config = ConfigDict(extra="forbid")

        text: str = Field(..., min_length=1)
        count: str | None = None
        category: str | None = None

    class Summary(RalphBaseModel):
        """High-level context and scope summary for the plan."""

        model_config = ConfigDict(extra="forbid")

        context: str = Field(..., min_length=1)
        scope_items: list[ScopeItem] = Field(..., min_length=3)

    class SkillsMcp(RalphBaseModel):
        """Skills and MCP servers required to execute the plan."""

        model_config = ConfigDict(extra="forbid")

        skills: list[str] = Field(default_factory=list)
        mcps: list[str] = Field(default_factory=list)

    class StepTarget(RalphBaseModel):
        """A file path and the action taken on it within a plan step.

        Step targets can be mutating (create/modify/delete) or non-mutating
        research/context actions (read/reference) so plans can point executors at
        exact source material without abusing critical_files.primary_files.
        """

        model_config = ConfigDict(extra="forbid")

        path: str = Field(..., min_length=1)
        action: Literal["create", "modify", "delete", "read", "reference"]

    class PlanStep(RalphBaseModel):
        """A single numbered implementation step within the plan."""

        model_config = ConfigDict(extra="forbid")

        number: int = Field(..., ge=1)
        title: str = Field(..., min_length=1)
        content: str = Field(..., min_length=1)
        step_type: Literal["file_change", "action", "research"] = "file_change"
        priority: Literal["critical", "high", "medium", "low"] | None = None
        targets: list[StepTarget] = Field(default_factory=list)
        location: str | None = None
        rationale: str | None = None
        depends_on: list[int] = Field(default_factory=list)

    class CriticalPrimaryFile(RalphBaseModel):
        """A primary file that will be created, modified, or deleted by the plan."""

        model_config = ConfigDict(extra="forbid")

        path: str = Field(..., min_length=1)
        action: Literal["create", "modify", "delete"]
        estimated_changes: str | None = None

    class ReferenceFile(RalphBaseModel):
        """A reference file consulted during implementation but not modified."""

        model_config = ConfigDict(extra="forbid")

        path: str = Field(..., min_length=1)
        purpose: str = Field(..., min_length=1)

    class CriticalFiles(RalphBaseModel):
        """All files touched by the plan, split into primary and reference groups."""

        model_config = ConfigDict(extra="forbid")

        primary_files: list[CriticalPrimaryFile] = Field(..., min_length=1)
        reference_files: list[ReferenceFile] = Field(default_factory=list)

    class RiskMitigation(RalphBaseModel):
        """A risk identified during planning together with its mitigation strategy."""

        model_config = ConfigDict(extra="forbid")

        risk: str = Field(..., min_length=1)
        mitigation: str = Field(..., min_length=1)
        severity: Literal["low", "medium", "high", "critical"] | None = None

    class VerificationStep(RalphBaseModel):
        """A single verification step with a method and expected outcome."""

        model_config = ConfigDict(extra="forbid")

        method: str = Field(..., min_length=1)
        expected_outcome: str = Field(..., min_length=1)

    class EditArea(RalphBaseModel):
        """File paths and directories edited by a parallel plan item."""

        model_config = ConfigDict(extra="forbid")

        paths: list[str] = Field(default_factory=list)
        directories: list[str] = Field(default_factory=list)


    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    edit_area: EditArea
    depends_on: list[str] = Field(default_factory=list)


ScopeItem = ParallelPlanItem.ScopeItem
Summary = ParallelPlanItem.Summary
SkillsMcp = ParallelPlanItem.SkillsMcp
StepTarget = ParallelPlanItem.StepTarget
PlanStep = ParallelPlanItem.PlanStep
CriticalPrimaryFile = ParallelPlanItem.CriticalPrimaryFile
ReferenceFile = ParallelPlanItem.ReferenceFile
CriticalFiles = ParallelPlanItem.CriticalFiles
RiskMitigation = ParallelPlanItem.RiskMitigation
VerificationStep = ParallelPlanItem.VerificationStep
EditArea = ParallelPlanItem.EditArea


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

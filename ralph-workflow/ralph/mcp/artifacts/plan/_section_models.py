"""Pydantic sub-models for plan artifacts.

This module is a thin re-export surface for the existing per-section
Pydantic models. It owns no behavior. The models themselves are defined
in their own ``_<modelname>.py`` files for fast import. The re-exports
keep the public ``ralph.mcp.artifacts.plan`` namespace stable so callers
can write ``from ralph.mcp.artifacts.plan import Summary`` without
having to know the file layout.

The module also defines the canonical ``PlanArtifactDict`` type alias
that downstream functions use to annotate normalized plan dicts. A
single named alias makes mypy catch drift when the schema evolves
instead of silently propagating the implicit ``dict[str, object]``
type through every helper.
"""

from __future__ import annotations

from ralph.mcp.artifacts.plan._acceptance_criteria import (
    AcceptanceCriteria,
    AcceptanceCriterion,
)
from ralph.mcp.artifacts.plan._critical_files import CriticalFiles
from ralph.mcp.artifacts.plan._critical_primary_file import CriticalPrimaryFile
from ralph.mcp.artifacts.plan._design_section import DesignSection
from ralph.mcp.artifacts.plan._edit_area import EditArea
from ralph.mcp.artifacts.plan._plan_step import PlanStep
from ralph.mcp.artifacts.plan._planning_profile import PlanningProfile
from ralph.mcp.artifacts.plan._reference_file import ReferenceFile
from ralph.mcp.artifacts.plan._risk_mitigation import RiskMitigation
from ralph.mcp.artifacts.plan._scope_category import ScopeCategory
from ralph.mcp.artifacts.plan._scope_item import ScopeItem
from ralph.mcp.artifacts.plan._skills_mcp import SkillsMcp
from ralph.mcp.artifacts.plan._step_target import StepTarget
from ralph.mcp.artifacts.plan._summary import Summary
from ralph.mcp.artifacts.plan._verification_step import VerificationStep
from ralph.mcp.artifacts.plan.plan_schema import ParallelPlanItem

PlanArtifactDict = dict[str, object]

__all__ = [
    "AcceptanceCriteria",
    "AcceptanceCriterion",
    "CriticalFiles",
    "CriticalPrimaryFile",
    "DesignSection",
    "EditArea",
    "ParallelPlanItem",
    "PlanArtifactDict",
    "PlanStep",
    "PlanningProfile",
    "ReferenceFile",
    "RiskMitigation",
    "ScopeCategory",
    "ScopeItem",
    "SkillsMcp",
    "StepTarget",
    "Summary",
    "VerificationStep",
]

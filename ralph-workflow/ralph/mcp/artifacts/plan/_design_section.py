"""Design section aggregating the seven SE-opinionated sub-models."""

from __future__ import annotations

from pydantic import ConfigDict, Field

from ralph.mcp.artifacts.plan._acceptance_criteria import AcceptanceCriteria
from ralph.mcp.artifacts.plan._dependency_injection import DependencyInjection
from ralph.mcp.artifacts.plan._design_constraints import DesignConstraints
from ralph.mcp.artifacts.plan._drift_detection import DriftDetection
from ralph.mcp.artifacts.plan._non_goals import NonGoals
from ralph.mcp.artifacts.plan._refactor_strategy import RefactorStrategy
from ralph.mcp.artifacts.plan._testability import Testability
from ralph.pydantic_compat import RalphBaseModel


class DesignSection(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    constraints: DesignConstraints | None = None
    non_goals: NonGoals | None = None
    dependency_injection: DependencyInjection | None = None
    drift_detection: DriftDetection | None = None
    testability: Testability | None = None
    refactor_strategy: RefactorStrategy | None = None
    acceptance_criteria: AcceptanceCriteria | None = None
    notes: str | None = Field(default=None)


__all__ = ["DesignSection"]

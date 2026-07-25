"""Design section aggregating optional, descriptive design guidance."""

from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator

from ralph.mcp.artifacts.plan._acceptance_criteria import AcceptanceCriteria
from ralph.mcp.artifacts.plan._dependency_injection import DependencyInjection
from ralph.mcp.artifacts.plan._design_constraints import DesignConstraints
from ralph.mcp.artifacts.plan._drift_detection import DriftDetection
from ralph.mcp.artifacts.plan._non_goals import NonGoals
from ralph.mcp.artifacts.plan._planning_profile import PlanningProfile
from ralph.mcp.artifacts.plan._refactor_strategy import RefactorStrategy
from ralph.mcp.artifacts.plan._testability import Testability
from ralph.pydantic_compat import RalphBaseModel


class DesignSection(RalphBaseModel):
    """Design section aggregating SE-opinionated sub-models.

    Collects cross-cutting design choices: planning profile, constraints,
    non-goals, dependency-injection expectations, drift-detection guards,
    testability requirements, refactor strategy, and acceptance criteria.
    ``planning_profile`` is descriptive and never fabricates missing
    sub-sections or project-specific execution requirements.
    """

    model_config = ConfigDict(extra="forbid")

    planning_profile: PlanningProfile | None = Field(
        default=None,
        min_length=1,
        max_length=200,
        description=("Free-form descriptive profile hint with no implicit defaults."),
    )
    constraints: DesignConstraints | None = None
    non_goals: NonGoals | None = None
    dependency_injection: DependencyInjection | None = None
    drift_detection: DriftDetection | None = None
    testability: Testability | None = None
    refactor_strategy: RefactorStrategy | None = None
    acceptance_criteria: AcceptanceCriteria | None = None
    outcome: str | None = Field(default=None, max_length=1000)
    notes: str | None = Field(default=None, max_length=20000)

    @field_validator("outcome")
    @classmethod
    def _strip_outcome(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None


__all__ = ["DesignSection"]

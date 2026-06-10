"""Design section aggregating the seven SE-opinionated sub-models.

The optional ``planning_profile`` field is a preset hint for cheap models. When
set, the @model_validator below bias-fills any None sub-section from a
class-level default dict. User-provided sub-section values always win; the
preset only fills in missing pieces. Sentinel ids (``PRESET-01``) and minimal
auto-fill behavior cannot collide with user-provided ``AC-XX`` / ``REF-XX``
entries because the prefix is distinct.
"""

from __future__ import annotations

from pydantic import ConfigDict, Field, field_validator, model_validator

from ralph.mcp.artifacts.plan._acceptance_criteria import (
    AcceptanceCriteria,
    AcceptanceCriterion,
)
from ralph.mcp.artifacts.plan._dependency_injection import DependencyInjection
from ralph.mcp.artifacts.plan._design_constraints import DesignConstraints
from ralph.mcp.artifacts.plan._drift_detection import DriftDetection
from ralph.mcp.artifacts.plan._non_goals import NonGoals
from ralph.mcp.artifacts.plan._planning_profile import PlanningProfile
from ralph.mcp.artifacts.plan._refactor_strategy import RefactorStrategy
from ralph.mcp.artifacts.plan._testability import Testability
from ralph.pydantic_compat import RalphBaseModel

_STRICT_DEFAULTS: dict[str, object] = {
    "testability": Testability(
        must_be_black_box=True,
        forbidden_in_tests=["time.sleep", "subprocess.run-no-timeout"],
        required_test_layers=["unit"],
    ),
    "dependency_injection": DependencyInjection(
        required_for_testability=True,
        forbidden_patterns=["global-singleton", "module-level-mutable-state"],
    ),
    "refactor_strategy": RefactorStrategy(
        approach="incremental",
        dead_code_policy="delete-immediately",
        allow_temporary_hacks=False,
    ),
    "drift_detection": DriftDetection(
        guard_commands=["ruff check ralph/", "uv run python -m mypy ralph/"],
        sources=["ruff", "mypy"],
        on_drift_action="fail-verify",
    ),
    "acceptance_criteria": AcceptanceCriteria(
        criteria=[
            AcceptanceCriterion(
                id="PRESET-01",
                description=(
                    "Strict preset placeholder - executor should replace with a real "
                    "acceptance criterion matching ^[A-Z]+-\\d{2,}$"
                ),
            )
        ]
    ),
}

_BALANCED_DEFAULTS: dict[str, object] = {
    "testability": _STRICT_DEFAULTS["testability"],
    "dependency_injection": _STRICT_DEFAULTS["dependency_injection"],
    "refactor_strategy": _STRICT_DEFAULTS["refactor_strategy"],
}

_PRESET_DEFAULTS: dict[PlanningProfile, dict[str, object]] = {
    "strict": _STRICT_DEFAULTS,
    "balanced": _BALANCED_DEFAULTS,
    "minimal": {},
}


class DesignSection(RalphBaseModel):
    model_config = ConfigDict(extra="forbid")

    planning_profile: PlanningProfile | None = None
    constraints: DesignConstraints | None = None
    non_goals: NonGoals | None = None
    dependency_injection: DependencyInjection | None = None
    drift_detection: DriftDetection | None = None
    testability: Testability | None = None
    refactor_strategy: RefactorStrategy | None = None
    acceptance_criteria: AcceptanceCriteria | None = None
    outcome: str | None = Field(default=None, max_length=500)
    notes: str | None = Field(default=None)

    @field_validator("outcome")
    @classmethod
    def _strip_outcome(cls, value: str | None) -> str | None:
        if value is None:
            return None
        stripped = value.strip()
        return stripped or None

    @model_validator(mode="after")
    def _bias_fill_from_profile(self) -> DesignSection:
        profile = self.planning_profile
        if profile is None:
            return self
        for key, default in _PRESET_DEFAULTS[profile].items():
            current: object = getattr(self, key)
            if current is None:
                setattr(self, key, default)
        return self


__all__ = ["DesignSection"]

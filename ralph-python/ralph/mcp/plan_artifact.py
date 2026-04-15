"""Structured planning artifact validation helpers."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, ValidationError

PLAN_ARTIFACT_TYPE = "plan"
PLAN_ARTIFACT_PATH = ".agent/artifacts/plan.json"


class PlanArtifactValidationError(ValueError):
    """Raised when a planning artifact does not match the formal schema."""


class ScopeItem(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    text: str = Field(..., min_length=1)
    count: str | None = None
    category: str | None = None


class Summary(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    context: str = Field(..., min_length=1)
    scope_items: list[ScopeItem] = Field(..., min_length=3)


class SkillsMcp(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    skills: list[str] = Field(default_factory=list)
    mcps: list[str] = Field(default_factory=list)


class StepTarget(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    action: Literal["create", "modify", "delete"]


class PlanStep(BaseModel):  # type: ignore[explicit-any]
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


class CriticalPrimaryFile(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    action: Literal["create", "modify", "delete"]
    estimated_changes: str | None = None


class ReferenceFile(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    path: str = Field(..., min_length=1)
    purpose: str = Field(..., min_length=1)


class CriticalFiles(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    primary_files: list[CriticalPrimaryFile] = Field(..., min_length=1)
    reference_files: list[ReferenceFile] = Field(default_factory=list)


class RiskMitigation(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    risk: str = Field(..., min_length=1)
    mitigation: str = Field(..., min_length=1)
    severity: Literal["low", "medium", "high", "critical"] | None = None


class VerificationStep(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    method: str = Field(..., min_length=1)
    expected_outcome: str = Field(..., min_length=1)


class EditArea(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    paths: list[str] = Field(default_factory=list)
    directories: list[str] = Field(default_factory=list)


class ParallelPlanItem(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    edit_area: EditArea
    depends_on: list[str] = Field(default_factory=list)


class PlanArtifact(BaseModel):  # type: ignore[explicit-any]
    model_config = ConfigDict(extra="forbid")

    summary: Summary
    skills_mcp: SkillsMcp | None = None
    steps: list[PlanStep] = Field(..., min_length=1)
    critical_files: CriticalFiles
    risks_mitigations: list[RiskMitigation] = Field(..., min_length=1)
    verification_strategy: list[VerificationStep] = Field(..., min_length=1)
    parallel_plan: list[ParallelPlanItem] = Field(default_factory=list)
    work_units: list[dict[str, object]] = Field(default_factory=list)


def normalize_plan_artifact_content(content: dict[str, object]) -> dict[str, object]:
    try:
        return PlanArtifact.model_validate(content).model_dump(
            mode="python",
            exclude_none=True,
            exclude_defaults=True,
        )
    except ValidationError as exc:
        raise PlanArtifactValidationError(_format_validation_error(exc)) from exc


def _format_validation_error(exc: ValidationError) -> str:
    first = exc.errors()[0]
    path = ".".join(str(part) for part in first.get("loc", ()) if part != "__root__")
    message = first.get("msg", "invalid plan artifact")
    return f"{path}: {message}" if path else str(message)


__all__ = [
    "PLAN_ARTIFACT_PATH",
    "PLAN_ARTIFACT_TYPE",
    "PlanArtifactValidationError",
    "normalize_plan_artifact_content",
]

"""Structured planning artifact validation helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Literal, cast

from loguru import logger
from pydantic import BaseModel, ConfigDict, Field, ValidationError

from ralph.mcp.file_backend import DEFAULT_FILE_BACKEND, FileBackend

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

PLAN_ARTIFACT_TYPE = "plan"
PLAN_ARTIFACT_PATH = ".agent/artifacts/plan.json"
PLAN_DRAFT_PATH = ".agent/artifacts/.plan_draft.json"
PLAN_DRAFT_SCHEMA_VERSION = 1

SectionMode = Literal["replace", "append"]


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


PLAN_SECTION_OBJECT_MODELS: dict[str, type[BaseModel]] = {
    "summary": Summary,
    "skills_mcp": SkillsMcp,
    "critical_files": CriticalFiles,
}

PLAN_SECTION_LIST_ITEM_MODELS: dict[str, type[BaseModel]] = {
    "steps": PlanStep,
    "risks_mitigations": RiskMitigation,
    "verification_strategy": VerificationStep,
    "parallel_plan": ParallelPlanItem,
}

PLAN_SECTION_NAMES: frozenset[str] = frozenset(
    set(PLAN_SECTION_OBJECT_MODELS) | set(PLAN_SECTION_LIST_ITEM_MODELS)
)


def is_noop_plan(artifact: Mapping[str, object]) -> bool:
    """Return True when ``artifact`` represents a planning no-op.

    An explicit ``noop: true`` marker is authoritative. As a defensive fallback,
    a plan with no ``steps`` and no ``work_units`` is also treated as a no-op
    so badly-shaped empty plans short-circuit cleanly instead of blowing up in
    schema validation downstream.
    """
    if artifact.get("noop") is True:
        return True
    steps = artifact.get("steps")
    work_units = artifact.get("work_units")
    steps_empty = steps is None or (isinstance(steps, list) and len(steps) == 0)
    work_units_empty = work_units is None or (
        isinstance(work_units, list) and len(work_units) == 0
    )
    # Only treat as noop fallback when both are explicitly empty lists — a plan
    # with no `steps` key at all is just malformed, not a deliberate no-op.
    return (
        steps_empty
        and work_units_empty
        and isinstance(steps, list)
        and isinstance(work_units, list)
    )


def normalize_plan_artifact_content(content: dict[str, object]) -> dict[str, object]:
    if is_noop_plan(content):
        return {"noop": True}
    try:
        validated = PlanArtifact.model_validate(content)
        return cast(
            "dict[str, object]",
            validated.model_dump(
                mode="python",
                exclude_none=True,
                exclude_defaults=True,
            ),
        )
    except ValidationError as exc:
        raise PlanArtifactValidationError(_format_validation_error(exc)) from exc


def _format_validation_error(exc: ValidationError) -> str:
    return str(exc)


def _dump_model(model: BaseModel) -> dict[str, object]:
    return cast(
        "dict[str, object]",
        model.model_dump(mode="python", exclude_none=True, exclude_defaults=True),
    )


def _validate_list_item(
    section: str, item_model: type[BaseModel], item: object
) -> dict[str, object]:
    if not isinstance(item, dict):
        raise PlanArtifactValidationError(f"section '{section}' items must be JSON objects")
    try:
        validated = item_model.model_validate(item)
    except ValidationError as exc:
        raise PlanArtifactValidationError(_format_validation_error(exc)) from exc
    return _dump_model(validated)


def validate_plan_section(
    section: str,
    payload: object,
    mode: SectionMode = "replace",
) -> object:
    """Validate a single plan section fragment against its submodel.

    Returns the normalized fragment (dict for object sections, list of dicts
    for list sections in replace mode, single dict for list sections in append
    mode). Raises PlanArtifactValidationError on any schema violation.
    """
    if section in PLAN_SECTION_OBJECT_MODELS:
        if mode != "replace":
            raise PlanArtifactValidationError(f"section '{section}' only supports mode='replace'")
        if not isinstance(payload, dict):
            raise PlanArtifactValidationError(f"section '{section}' must be a JSON object")
        model = PLAN_SECTION_OBJECT_MODELS[section]
        try:
            validated = model.model_validate(payload)
        except ValidationError as exc:
            raise PlanArtifactValidationError(_format_validation_error(exc)) from exc
        return _dump_model(validated)

    if section in PLAN_SECTION_LIST_ITEM_MODELS:
        item_model = PLAN_SECTION_LIST_ITEM_MODELS[section]
        if mode == "replace":
            if not isinstance(payload, list):
                raise PlanArtifactValidationError(
                    f"section '{section}' with mode='replace' must be a JSON array"
                )
            return [_validate_list_item(section, item_model, item) for item in payload]
        if mode == "append":
            return _validate_list_item(section, item_model, payload)
        raise PlanArtifactValidationError(f"unknown mode '{mode}' for section '{section}'")

    raise PlanArtifactValidationError(
        f"unknown plan section '{section}'. Valid sections: {sorted(PLAN_SECTION_NAMES)}"
    )


def merge_plan_section(
    sections: dict[str, object],
    section: str,
    fragment: object,
    mode: SectionMode,
) -> dict[str, object]:
    """Return a new sections dict with the given fragment merged in."""
    new_sections: dict[str, object] = dict(sections)
    if section in PLAN_SECTION_OBJECT_MODELS or mode == "replace":
        new_sections[section] = fragment
        return new_sections

    existing = new_sections.get(section)
    base: list[object] = list(existing) if isinstance(existing, list) else []
    base.append(fragment)
    new_sections[section] = base
    return new_sections


def finalize_plan_draft(draft: dict[str, object]) -> dict[str, object]:
    """Validate a draft's sections as a whole PlanArtifact.

    Raises PlanArtifactValidationError if any cross-section invariant fails
    (e.g. a required section is still missing).
    """
    sections = draft.get("sections")
    if not isinstance(sections, dict):
        raise PlanArtifactValidationError("plan draft is missing a 'sections' object")
    return normalize_plan_artifact_content(cast("dict[str, object]", sections))


def _now_iso() -> str:
    return datetime.now(tz=UTC).isoformat()


def new_plan_draft(*, now_iso: Callable[[], str] = _now_iso) -> dict[str, object]:
    now = now_iso()
    return {
        "schema_version": PLAN_DRAFT_SCHEMA_VERSION,
        "started_at": now,
        "updated_at": now,
        "sections": {},
    }


def load_plan_draft(
    artifact_dir: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> dict[str, object] | None:
    """Read the plan draft file if present and parseable. None otherwise."""
    draft_path = artifact_dir / ".plan_draft.json"
    if not backend.exists(draft_path):
        return None
    try:
        raw = backend.read_text(draft_path, encoding="utf-8")
        parsed = cast("object", json.loads(raw))
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read plan draft at {}: {}", draft_path, exc)
        return None
    if not isinstance(parsed, dict):
        logger.warning("Plan draft at {} is not a JSON object", draft_path)
        return None
    parsed_dict = cast("dict[str, object]", parsed)
    if not isinstance(parsed_dict.get("sections"), dict):
        logger.warning("Plan draft at {} has no 'sections' object", draft_path)
        return None
    return parsed_dict


def save_plan_draft(
    artifact_dir: Path,
    draft: dict[str, object],
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
    now_iso: Callable[[], str] = _now_iso,
) -> None:
    """Atomically write the plan draft file."""
    backend.mkdir(artifact_dir, parents=True, exist_ok=True)
    draft_path = artifact_dir / ".plan_draft.json"
    tmp_path = draft_path.with_suffix(".json.tmp")
    serialized_draft = dict(draft)
    serialized_draft["updated_at"] = now_iso()
    serialized = json.dumps(serialized_draft, indent=2, sort_keys=False)
    backend.write_text(tmp_path, serialized, encoding="utf-8")
    backend.replace(tmp_path, draft_path)


def delete_plan_draft(artifact_dir: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND) -> bool:
    """Remove the plan draft file. Returns True if it existed."""
    draft_path = artifact_dir / ".plan_draft.json"
    if not backend.exists(draft_path):
        return False
    backend.unlink(draft_path)
    return True


__all__ = [
    "PLAN_ARTIFACT_PATH",
    "PLAN_ARTIFACT_TYPE",
    "PLAN_DRAFT_PATH",
    "PLAN_DRAFT_SCHEMA_VERSION",
    "PLAN_SECTION_LIST_ITEM_MODELS",
    "PLAN_SECTION_NAMES",
    "PLAN_SECTION_OBJECT_MODELS",
    "PlanArtifactValidationError",
    "SectionMode",
    "delete_plan_draft",
    "finalize_plan_draft",
    "is_noop_plan",
    "load_plan_draft",
    "merge_plan_section",
    "new_plan_draft",
    "normalize_plan_artifact_content",
    "save_plan_draft",
    "validate_plan_section",
]

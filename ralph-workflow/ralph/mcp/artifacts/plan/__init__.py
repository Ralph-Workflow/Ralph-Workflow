"""Structured planning artifact validation helpers."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from importlib import import_module
from typing import TYPE_CHECKING, Literal, cast

from loguru import logger
from pydantic import ConfigDict, Field, ValidationError

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND, FileBackend
from ralph.mcp.artifacts.plan.plan_artifact_validation_error import PlanArtifactValidationError
from ralph.pydantic_compat import RalphBaseModel

from .plan_schema import (
    AcceptanceCriteria,
    AcceptanceCriterion,
    CriticalFiles,
    CriticalPrimaryFile,
    DesignSection,
    EditArea,
    ParallelPlanItem,
    PlanStep,
    ReferenceFile,
    RiskMitigation,
    ScopeItem,
    SkillsMcp,
    StepTarget,
    Summary,
    VerificationStep,
)

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping
    from pathlib import Path

PLAN_ARTIFACT_TYPE = "plan"
PLAN_ARTIFACT_PATH = ".agent/artifacts/plan.json"
PLAN_MARKDOWN_PATH = ".agent/PLAN.md"
PLAN_DRAFT_PATH = ".agent/artifacts/.plan_draft.json"
PLAN_DRAFT_SCHEMA_VERSION = 1

SectionMode = Literal["replace", "append"]


class PlanArtifact(RalphBaseModel):
    """Top-level validated schema for a plan artifact."""

    model_config = ConfigDict(extra="forbid")

    summary: Summary
    skills_mcp: SkillsMcp
    steps: list[PlanStep] = Field(..., min_length=1)
    critical_files: CriticalFiles
    risks_mitigations: list[RiskMitigation] = Field(..., min_length=1)
    design: DesignSection | None = None
    verification_strategy: list[VerificationStep] = Field(..., min_length=1)
    parallel_plan: list[ParallelPlanItem] = Field(default_factory=list)
    work_units: list[dict[str, object]] = Field(default_factory=list)


PLAN_SECTION_OBJECT_MODELS: dict[str, type[RalphBaseModel]] = {
    "summary": Summary,
    "skills_mcp": SkillsMcp,
    "critical_files": CriticalFiles,
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
    work_units_empty = work_units is None or (isinstance(work_units, list) and len(work_units) == 0)
    return (
        steps_empty
        and work_units_empty
        and isinstance(steps, list)
        and isinstance(work_units, list)
    )


def normalize_plan_artifact_content(content: dict[str, object]) -> dict[str, object]:
    """Validate and normalize a raw plan artifact content dict."""
    if is_noop_plan(content):
        return {"noop": True}
    try:
        validated = PlanArtifact.model_validate(content)
        return validated.model_dump(
            mode="python",
            exclude_none=True,
            exclude_defaults=True,
        )
    except ValidationError as exc:
        raise PlanArtifactValidationError(_format_validation_error(exc)) from exc


def _format_validation_error(exc: ValidationError) -> str:
    return str(exc)


def _dump_model(model: RalphBaseModel) -> dict[str, object]:
    return model.model_dump(mode="python", exclude_none=True, exclude_defaults=True)


def _validate_list_item(
    section: str, item_model: type[RalphBaseModel], item: object
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

    if section == "work_units":
        work_unit_model = cast(
            "type[RalphBaseModel]", import_module("ralph.pipeline.work_units").WorkUnit
        )
        if mode == "replace":
            if not isinstance(payload, list):
                raise PlanArtifactValidationError(
                    "section 'work_units' with mode='replace' must be a JSON array"
                )
            return [_validate_list_item(section, work_unit_model, item) for item in payload]
        if mode == "append":
            return _validate_list_item(section, work_unit_model, payload)
        raise PlanArtifactValidationError(f"unknown mode '{mode}' for section '{section}'")

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


def _steps_from_sections(sections: dict[str, object]) -> list[dict[str, object]]:
    raw_steps = sections.get("steps")
    if raw_steps is None:
        return []
    if not isinstance(raw_steps, list):
        raise PlanArtifactValidationError("section 'steps' must be a JSON array")

    validated = validate_plan_section("steps", raw_steps, mode="replace")
    return cast("list[dict[str, object]]", validated)


def _normalize_step_edit_payload(
    step_payload: object,
    *,
    synthetic_number: int,
) -> dict[str, object]:
    if not isinstance(step_payload, dict):
        raise PlanArtifactValidationError("step payload must be a JSON object")
    payload = dict(cast("dict[str, object]", step_payload))
    payload["number"] = synthetic_number
    fragment = validate_plan_section("steps", payload, mode="append")
    return cast("dict[str, object]", fragment)


def _reindex_plan_steps(
    steps: list[dict[str, object]],
    *,
    removed_step_number: int | None = None,
) -> list[dict[str, object]]:
    old_numbers = [cast("int", step["number"]) for step in steps]
    if len(set(old_numbers)) != len(old_numbers):
        raise PlanArtifactValidationError("plan steps must have unique step numbers before edit")

    number_map = {old_number: index for index, old_number in enumerate(old_numbers, start=1)}
    updated_steps: list[dict[str, object]] = []
    for step, old_number in zip(steps, old_numbers, strict=False):
        remapped = dict(step)
        remapped["number"] = number_map[old_number]
        dependencies = step.get("depends_on", [])
        if not isinstance(dependencies, list):
            raise PlanArtifactValidationError("step depends_on must be a JSON array")

        remapped_dependencies: list[int] = []
        for dependency in dependencies:
            if not isinstance(dependency, int):
                raise PlanArtifactValidationError("step depends_on must contain integers")
            if removed_step_number is not None and dependency == removed_step_number:
                raise PlanArtifactValidationError(
                    "cannot remove step "
                    f"{removed_step_number}; another step depends on step "
                    f"{removed_step_number}"
                )
            mapped_dependency = number_map.get(dependency)
            if mapped_dependency is None:
                raise PlanArtifactValidationError(
                    f"step {old_number} depends on unknown step {dependency}"
                )
            remapped_dependencies.append(mapped_dependency)
        remapped["depends_on"] = remapped_dependencies
        updated_steps.append(remapped)

    normalized = validate_plan_section("steps", updated_steps, mode="replace")
    return cast("list[dict[str, object]]", normalized)


def insert_plan_step(
    sections: dict[str, object],
    *,
    index: int,
    step_payload: object,
) -> dict[str, object]:
    steps = _steps_from_sections(sections)
    if index < 1 or index > len(steps) + 1:
        raise PlanArtifactValidationError(
            f"step insert index must be between 1 and {len(steps) + 1}"
        )

    existing_numbers = [cast("int", step["number"]) for step in steps]
    inserted_step = _normalize_step_edit_payload(
        step_payload,
        synthetic_number=max(existing_numbers, default=0) + 1,
    )
    steps.insert(index - 1, inserted_step)
    updated_sections = dict(sections)
    updated_sections["steps"] = _reindex_plan_steps(steps)
    return updated_sections


def replace_plan_step(
    sections: dict[str, object],
    *,
    step_number: int,
    step_payload: object,
) -> dict[str, object]:
    steps = _steps_from_sections(sections)
    target_index = next(
        (idx for idx, step in enumerate(steps) if step["number"] == step_number),
        None,
    )
    if target_index is None:
        raise PlanArtifactValidationError(f"step {step_number} does not exist")

    existing_numbers = [cast("int", step["number"]) for step in steps]
    replacement_step = _normalize_step_edit_payload(
        step_payload,
        synthetic_number=max(existing_numbers, default=0) + 1,
    )
    steps[target_index] = replacement_step
    updated_sections = dict(sections)
    updated_sections["steps"] = _reindex_plan_steps(steps)
    return updated_sections


def remove_plan_step(
    sections: dict[str, object],
    *,
    step_number: int,
) -> dict[str, object]:
    steps = _steps_from_sections(sections)
    remaining_steps = [step for step in steps if step["number"] != step_number]
    if len(remaining_steps) == len(steps):
        raise PlanArtifactValidationError(f"step {step_number} does not exist")

    updated_sections = dict(sections)
    updated_sections["steps"] = _reindex_plan_steps(
        remaining_steps,
        removed_step_number=step_number,
    )
    return updated_sections


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
    """Return a fresh plan draft with empty sections and timestamps."""
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


def load_plan_artifact_sections(
    artifact_dir: Path, *, backend: FileBackend = DEFAULT_FILE_BACKEND
) -> dict[str, object] | None:
    """Load the normalized sections from a finalized plan artifact if present."""
    plan_path = artifact_dir / "plan.json"
    if not backend.exists(plan_path):
        return None

    result: dict[str, object] | None = None
    try:
        raw = backend.read_text(plan_path, encoding="utf-8")
        parsed = cast("object", json.loads(raw))
        if not isinstance(parsed, dict):
            logger.warning("Plan artifact at {} is not a JSON object", plan_path)
            return None
        parsed_dict = cast("dict[str, object]", parsed)
        content = parsed_dict.get("content") if parsed_dict.get("type") == "plan" else parsed_dict
        if not isinstance(content, dict):
            logger.warning("Plan artifact at {} has no valid 'content' object", plan_path)
            return None
        normalized = normalize_plan_artifact_content(cast("dict[str, object]", content))
        if normalized.get("noop") is not True:
            result = normalized
    except (OSError, json.JSONDecodeError) as exc:
        logger.warning("Failed to read plan artifact at {}: {}", plan_path, exc)
    except PlanArtifactValidationError as exc:
        logger.warning(
            "Plan artifact at {} failed validation for draft hydration: {}",
            plan_path,
            exc,
        )

    return result


def render_plan_markdown(content: Mapping[str, object]) -> str:
    """Render the structured plan artifact as agent-facing Markdown."""
    plan = normalize_plan_artifact_content(cast("dict[str, object]", dict(content)))
    if plan.get("noop") is True:
        return "# Execution Plan\n\nNo execution work is required.\n"

    lines = ["# Execution Plan"]
    lines.extend(_render_summary_section(plan.get("summary")))
    lines.extend(_render_skills_mcp_section(plan.get("skills_mcp")))
    lines.extend(_render_steps_section(plan.get("steps")))
    lines.extend(_render_critical_files_section(plan.get("critical_files")))
    lines.extend(_render_risks_section(plan.get("risks_mitigations")))
    lines.extend(_render_design_section(plan.get("design")))
    lines.extend(_render_verification_section(plan.get("verification_strategy")))
    lines.extend(_render_parallel_plan_section(plan.get("parallel_plan")))
    lines.extend(_render_work_units_section(plan.get("work_units")))
    return "\n".join(lines).rstrip() + "\n"


def _render_summary_section(summary: object) -> list[str]:
    if not isinstance(summary, dict):
        return []

    lines: list[str] = []
    context = summary.get("context")
    if isinstance(context, str) and context.strip():
        lines.extend(["", "## Summary", "", context.strip()])

    scope_items = summary.get("scope_items")
    if isinstance(scope_items, list) and scope_items:
        lines.extend(["", "## Scope"])
        for item in scope_items:
            if not isinstance(item, dict):
                continue
            text = item.get("text")
            if isinstance(text, str) and text.strip():
                lines.extend(["", f"- {text.strip()}"])
    return lines


def _string_list(value: object) -> list[str]:
    if not isinstance(value, list):
        return []
    return [entry.strip() for entry in value if isinstance(entry, str) and entry.strip()]


def _render_skills_mcp_section(skills_mcp: object) -> list[str]:
    if not isinstance(skills_mcp, dict):
        return []

    skill_lines = [f"- `{name}`" for name in _string_list(skills_mcp.get("skills"))]
    mcp_lines = [f"- `{name}`" for name in _string_list(skills_mcp.get("mcps"))]
    if not skill_lines and not mcp_lines:
        return []

    lines = ["", "## Skills and MCPs"]
    if skill_lines:
        lines.extend(["", "### Skills", ""])
        lines.extend(skill_lines)
    if mcp_lines:
        lines.extend(["", "### MCP Servers", ""])
        lines.extend(mcp_lines)
    return lines


def extract_plan_payload(content: Mapping[str, object]) -> dict[str, object] | None:
    """Return the raw plan dict from an artifact envelope or bare payload."""
    if content.get("type") == "plan":
        nested = content.get("content")
        if isinstance(nested, dict):
            return cast("dict[str, object]", nested)
        return None
    return cast("dict[str, object]", dict(content))


def extract_plan_skill_names(content: Mapping[str, object]) -> tuple[str, ...]:
    """Return planner-specified skill names from a plan artifact payload."""
    plan = extract_plan_payload(content)
    if plan is None:
        return ()
    skills_mcp = plan.get("skills_mcp")
    if not isinstance(skills_mcp, dict):
        return ()
    skills = skills_mcp.get("skills")
    if not isinstance(skills, list):
        return ()
    names = tuple(
        entry.strip()
        for entry in skills
        if isinstance(entry, str) and entry.strip()
    )
    return names


def _render_steps_section(steps: object) -> list[str]:
    if not isinstance(steps, list) or not steps:
        return []

    lines = ["", "## Steps"]
    for step in steps:
        if not isinstance(step, dict):
            continue
        lines.extend(_render_step_entry(step))
    return lines


def _render_step_entry(step: Mapping[str, object]) -> list[str]:
    number = step.get("number", "?")
    title = step.get("title", "Untitled step")
    lines = ["", f"{number}. **{title}**"]
    content_text = step.get("content")
    if isinstance(content_text, str) and content_text.strip():
        lines.extend(["", f"   {content_text.strip()}"])

    targets = step.get("targets")
    if isinstance(targets, list) and targets:
        lines.extend(["", "   Targets:"])
        for target in targets:
            if not isinstance(target, dict):
                continue
            path = target.get("path")
            action = target.get("action")
            if isinstance(path, str) and isinstance(action, str):
                lines.extend(["", f"   - `{path}` ({action})"])
    return lines


def _render_critical_files_section(critical_files: object) -> list[str]:
    if not isinstance(critical_files, dict):
        return []
    primary_files = critical_files.get("primary_files")
    if not isinstance(primary_files, list) or not primary_files:
        return []

    lines = ["", "## Critical Files"]
    for file_info in primary_files:
        if not isinstance(file_info, dict):
            continue
        path = file_info.get("path")
        action = file_info.get("action")
        if isinstance(path, str) and isinstance(action, str):
            lines.extend(["", f"- `{path}` ({action})"])
    return lines


def _render_risks_section(risks: object) -> list[str]:
    if not isinstance(risks, list) or not risks:
        return []

    lines = ["", "## Risks and Mitigations"]
    for risk in risks:
        if not isinstance(risk, dict):
            continue
        risk_text = risk.get("risk")
        mitigation = risk.get("mitigation")
        if isinstance(risk_text, str) and isinstance(mitigation, str):
            lines.extend(["", f"- **Risk:** {risk_text}", f"  **Mitigation:** {mitigation}"])
    return lines


def _render_design_section(design: object) -> list[str]:
    if not isinstance(design, dict):
        return []

    sub_section_order: tuple[tuple[str, str, Callable[[Mapping[str, object]], list[str]]], ...] = (
        ("constraints", "Design Constraints", _render_design_constraints_block),
        ("non_goals", "Non-Goals", _render_design_non_goals_block),
        ("dependency_injection", "Dependency Injection", _render_design_di_block),
        ("drift_detection", "Drift Detection", _render_design_drift_block),
        ("testability", "Testability", _render_design_testability_block),
        ("refactor_strategy", "Refactor Strategy", _render_design_refactor_block),
        ("acceptance_criteria", "Acceptance Criteria", _render_design_acceptance_block),
    )

    rendered: list[str] = []
    for key, heading, renderer in sub_section_order:
        sub = design.get(key)
        if not isinstance(sub, dict):
            continue
        sub_lines = renderer(cast("Mapping[str, object]", sub))
        if not sub_lines:
            continue
        rendered.append("")
        rendered.append(f"### {heading}")
        rendered.append("")
        rendered.extend(sub_lines)

    notes = design.get("notes")
    if isinstance(notes, str) and notes.strip():
        rendered.extend(["", "### Notes", "", notes.strip()])

    if not rendered:
        return []
    rendered.insert(0, "")
    rendered.insert(1, "## Design")
    return rendered


def _render_design_constraints_block(constraints: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    text = constraints.get("text")
    if isinstance(text, str) and text.strip():
        lines.append(f"- text: {text.strip()}")
    invariants = constraints.get("invariants")
    if isinstance(invariants, list):
        lines.extend(
            f"  - invariant: {entry.strip()}"
            for entry in invariants
            if isinstance(entry, str) and entry.strip()
        )
    style = constraints.get("architecture_style")
    if isinstance(style, str) and style.strip():
        lines.append(f"- architecture_style: {style.strip()}")
    return lines


def _render_design_non_goals_block(non_goals: Mapping[str, object]) -> list[str]:
    items = non_goals.get("items")
    if not isinstance(items, list):
        return []
    return [
        f"- {entry.strip()}"
        for entry in items
        if isinstance(entry, str) and entry.strip()
    ]


def _render_design_di_block(di: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    required = di.get("required_for_testability")
    if isinstance(required, bool):
        lines.append(f"- required_for_testability: {'true' if required else 'false'}")
    preferred = di.get("preferred_patterns")
    if isinstance(preferred, list):
        lines.extend(
            f"  - preferred_pattern: {entry.strip()}"
            for entry in preferred
            if isinstance(entry, str) and entry.strip()
        )
    forbidden = di.get("forbidden_patterns")
    if isinstance(forbidden, list):
        lines.extend(
            f"  - forbidden_pattern: {entry.strip()}"
            for entry in forbidden
            if isinstance(entry, str) and entry.strip()
        )
    notes = di.get("notes")
    if isinstance(notes, str) and notes.strip():
        lines.append(f"- notes: {notes.strip()}")
    return lines


def _render_design_drift_block(drift: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    commands = drift.get("guard_commands")
    if isinstance(commands, list):
        lines.extend(
            f"- guard_command: `{entry.strip()}`"
            for entry in commands
            if isinstance(entry, str) and entry.strip()
        )
    expected = drift.get("expected_outputs")
    if isinstance(expected, list):
        lines.extend(
            f"  - expected_output: {entry.strip()}"
            for entry in expected
            if isinstance(entry, str) and entry.strip()
        )
    sources = drift.get("sources")
    if isinstance(sources, list):
        lines.extend(
            f"  - source: {entry.strip()}"
            for entry in sources
            if isinstance(entry, str) and entry.strip()
        )
    action = drift.get("on_drift_action")
    if isinstance(action, str) and action.strip():
        lines.append(f"- on_drift_action: {action.strip()}")
    return lines


def _render_design_testability_block(testability: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    black_box = testability.get("must_be_black_box")
    if isinstance(black_box, bool):
        lines.append(f"- must_be_black_box: {'true' if black_box else 'false'}")
    forbidden = testability.get("forbidden_in_tests")
    if isinstance(forbidden, list):
        lines.extend(
            f"  - forbidden_in_tests: {entry.strip()}"
            for entry in forbidden
            if isinstance(entry, str) and entry.strip()
        )
    layers = testability.get("required_test_layers")
    if isinstance(layers, list):
        lines.extend(
            f"  - required_test_layer: {entry.strip()}"
            for entry in layers
            if isinstance(entry, str) and entry.strip()
        )
    clock_required = testability.get("clock_injection_required")
    if isinstance(clock_required, bool):
        lines.append(f"- clock_injection_required: {'true' if clock_required else 'false'}")
    max_unit = testability.get("max_unit_test_seconds")
    if isinstance(max_unit, (int, float)) and not isinstance(max_unit, bool):
        lines.append(f"- max_unit_test_seconds: {float(max_unit):g}")
    return lines


def _render_design_refactor_block(refactor: Mapping[str, object]) -> list[str]:
    lines: list[str] = []
    approach = refactor.get("approach")
    if isinstance(approach, str) and approach.strip():
        lines.append(f"- approach: {approach.strip()}")
    preserve = refactor.get("preserve_public_api")
    if isinstance(preserve, bool):
        lines.append(f"- preserve_public_api: {'true' if preserve else 'false'}")
    policy = refactor.get("dead_code_policy")
    if isinstance(policy, str) and policy.strip():
        lines.append(f"- dead_code_policy: {policy.strip()}")
    hacks = refactor.get("allow_temporary_hacks")
    if isinstance(hacks, bool):
        lines.append(f"- allow_temporary_hacks: {'true' if hacks else 'false'}")
    return lines


def _render_design_acceptance_block(acceptance: Mapping[str, object]) -> list[str]:
    criteria = acceptance.get("criteria")
    if not isinstance(criteria, list):
        return []
    lines: list[str] = []
    for entry in criteria:
        if not isinstance(entry, dict):
            continue
        cid = entry.get("id")
        desc = entry.get("description")
        if isinstance(cid, str) and isinstance(desc, str) and cid.strip() and desc.strip():
            lines.append(f"- {cid.strip()} — {desc.strip()}")
    return lines


def _render_verification_section(verification: object) -> list[str]:
    if not isinstance(verification, list) or not verification:
        return []

    lines = ["", "## Verification"]
    for check in verification:
        if not isinstance(check, dict):
            continue
        method = check.get("method")
        outcome = check.get("expected_outcome")
        if isinstance(method, str) and isinstance(outcome, str):
            lines.extend(["", f"- `{method}` — {outcome}"])
    return lines


def _render_parallel_plan_section(parallel_plan: object) -> list[str]:
    return _render_named_items_section(
        parallel_plan,
        heading="## Parallel Plan",
        id_key="id",
        description_key="description",
    )


def _render_work_units_section(work_units: object) -> list[str]:
    return _render_named_items_section(
        work_units,
        heading="## Work Units",
        id_key="unit_id",
        description_key="description",
    )


def _render_named_items_section(
    items: object,
    *,
    heading: str,
    id_key: str,
    description_key: str,
) -> list[str]:
    if not isinstance(items, list) or not items:
        return []

    lines = ["", heading]
    for item in items:
        if not isinstance(item, dict):
            continue
        item_id = item.get(id_key)
        description = item.get(description_key)
        if isinstance(item_id, str) and isinstance(description, str):
            lines.extend(["", f"- **{item_id}** — {description}"])
    return lines


def write_plan_markdown(
    workspace_root: Path,
    content: Mapping[str, object],
    *,
    backend: FileBackend = DEFAULT_FILE_BACKEND,
) -> None:
    """Persist the agent-facing Markdown handoff for a normalized plan."""
    destination = workspace_root / ".agent" / "PLAN.md"
    backend.mkdir(destination.parent, parents=True, exist_ok=True)
    backend.write_text(destination, render_plan_markdown(content), encoding="utf-8")


__all__ = [
    "PLAN_ARTIFACT_PATH",
    "PLAN_ARTIFACT_TYPE",
    "PLAN_DRAFT_PATH",
    "PLAN_DRAFT_SCHEMA_VERSION",
    "PLAN_MARKDOWN_PATH",
    "PLAN_SECTION_LIST_ITEM_MODELS",
    "PLAN_SECTION_NAMES",
    "PLAN_SECTION_OBJECT_MODELS",
    "AcceptanceCriteria",
    "AcceptanceCriterion",
    "CriticalFiles",
    "CriticalPrimaryFile",
    "DesignSection",
    "EditArea",
    "ParallelPlanItem",
    "PlanArtifact",
    "PlanArtifactValidationError",
    "PlanStep",
    "ReferenceFile",
    "RiskMitigation",
    "ScopeItem",
    "SectionMode",
    "SkillsMcp",
    "StepTarget",
    "Summary",
    "VerificationStep",
    "delete_plan_draft",
    "extract_plan_payload",
    "extract_plan_skill_names",
    "finalize_plan_draft",
    "insert_plan_step",
    "is_noop_plan",
    "load_plan_artifact_sections",
    "load_plan_draft",
    "merge_plan_section",
    "new_plan_draft",
    "normalize_plan_artifact_content",
    "remove_plan_step",
    "render_plan_markdown",
    "replace_plan_step",
    "save_plan_draft",
    "validate_plan_section",
    "write_plan_markdown",
]

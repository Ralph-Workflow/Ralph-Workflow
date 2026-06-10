"""Step-level edits against a staged plan draft.

The three public entry points ``insert_plan_step``, ``replace_plan_step``,
and ``remove_plan_step`` accept a ``sections`` dict (the current
contents of the staged draft) and return a new ``sections`` dict with
the change applied. They automatically reindex the full steps list AND
remap the cross-references in ``design.acceptance_criteria.criteria``
(``satisfied_by_steps``) so the step<->AC graph stays in lockstep.

The five private helpers
(``_steps_from_sections``, ``_normalize_step_edit_payload``,
``_reindex_plan_steps``, ``_remap_ac_step_refs``) are the building
blocks the three public entry points share.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.plan._validation import (
    PlanArtifactValidationError,
    validate_plan_section,
)

if TYPE_CHECKING:
    from ralph.mcp.artifacts.plan._section_models import PlanArtifactDict


def _steps_from_sections(sections: PlanArtifactDict) -> list[dict[str, object]]:
    raw_steps = sections.get("steps")
    if raw_steps is None:
        return []
    if not isinstance(raw_steps, list):
        raise PlanArtifactValidationError("section 'steps' must be a JSON array")

    validated = validate_plan_section("steps", raw_steps, mode="replace")
    return cast("list[dict[str, object]]", validated)


def _remap_ac_step_refs(
    sections: PlanArtifactDict,
    number_map: dict[int, int],
) -> PlanArtifactDict:
    """Keep ``AC.satisfied_by_steps`` in lockstep with the new step numbering.

    Entries that no longer point at a real step are dropped silently; entries
    that survive the reindex are translated through ``number_map``. This is
    the principle-of-least-surprise behavior for the executor: when step 2 is
    removed, the AC that listed step 2 as a satisfier no longer claims to be
    satisfied by it, but the rest of the AC graph is preserved.
    """
    new_sections: dict[str, object] = dict(sections)
    design = new_sections.get("design")
    if not isinstance(design, dict):
        return new_sections
    ac = design.get("acceptance_criteria")
    if not isinstance(ac, dict):
        return new_sections
    criteria = ac.get("criteria")
    if not isinstance(criteria, list):
        return new_sections
    kept_new_numbers = set(number_map.values())
    new_criteria: list[object] = []
    for entry in criteria:
        if not isinstance(entry, dict):
            new_criteria.append(entry)
            continue
        refs = entry.get("satisfied_by_steps")
        if not isinstance(refs, list):
            new_criteria.append(entry)
            continue
        remapped: list[int] = []
        for ref in refs:
            if not isinstance(ref, int) or ref not in number_map:
                continue
            new_number = number_map[ref]
            if new_number not in kept_new_numbers:
                continue
            remapped.append(new_number)
        new_entry: dict[str, object] = dict(cast("dict[str, object]", entry))
        new_entry["satisfied_by_steps"] = remapped
        new_criteria.append(new_entry)
    new_ac_dict: dict[str, object] = dict(cast("dict[str, object]", ac))
    new_ac_dict["criteria"] = new_criteria
    new_design_dict: dict[str, object] = dict(cast("dict[str, object]", design))
    new_design_dict["acceptance_criteria"] = new_ac_dict
    new_sections["design"] = new_design_dict
    return new_sections


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
) -> tuple[list[dict[str, object]], dict[int, int]]:
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
    return cast("list[dict[str, object]]", normalized), number_map


def insert_plan_step(
    sections: PlanArtifactDict,
    *,
    index: int,
    step_payload: object,
) -> PlanArtifactDict:
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
    reindexed_steps, number_map = _reindex_plan_steps(steps)
    updated_sections = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections = _remap_ac_step_refs(updated_sections, number_map)
    return updated_sections


def replace_plan_step(
    sections: PlanArtifactDict,
    *,
    step_number: int,
    step_payload: object,
) -> PlanArtifactDict:
    steps = _steps_from_sections(sections)
    target_index = next(
        (idx for idx, step in enumerate(steps) if step["number"] == step_number),
        None,
    )
    if target_index is None:
        raise PlanArtifactValidationError(f"step {step_number} does not exist")

    replacement_step = _normalize_step_edit_payload(
        step_payload,
        synthetic_number=step_number,
    )
    steps[target_index] = replacement_step
    reindexed_steps, number_map = _reindex_plan_steps(steps)
    updated_sections = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections = _remap_ac_step_refs(updated_sections, number_map)
    return updated_sections


def remove_plan_step(
    sections: PlanArtifactDict,
    *,
    step_number: int,
) -> PlanArtifactDict:
    steps = _steps_from_sections(sections)
    remaining_steps = [step for step in steps if step["number"] != step_number]
    if len(remaining_steps) == len(steps):
        raise PlanArtifactValidationError(f"step {step_number} does not exist")

    reindexed_steps, number_map = _reindex_plan_steps(
        remaining_steps,
        removed_step_number=step_number,
    )
    updated_sections = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections = _remap_ac_step_refs(updated_sections, number_map)
    return updated_sections


__all__ = [
    "insert_plan_step",
    "remove_plan_step",
    "replace_plan_step",
]

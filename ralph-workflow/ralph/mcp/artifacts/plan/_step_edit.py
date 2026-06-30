"""Step-level edits against a staged plan draft.

The four public entry points ``insert_plan_step``, ``replace_plan_step``,
``remove_plan_step``, and ``move_plan_step`` accept a ``sections`` dict
(the current contents of the staged draft) and return a new
``sections`` dict with the change applied. They automatically reindex
the full steps list AND remap the cross-references in
``design.acceptance_criteria.criteria`` (``satisfied_by_steps``) so the
step<->AC graph stays in lockstep.

The five private helpers
(``_steps_from_sections``, ``_normalize_step_edit_payload``,
``_reindex_plan_steps``, ``_remap_ac_step_refs``) are the building
blocks the four public entry points share.

The ``_build_step_mutation_echo`` helper consolidates the reindex map
and the rewritten/dropped AC id lists into the JSON payload the
step-mutation tool handlers return. The handler-side effect (rewriting
the draft, returning the echo) is small so the helpers can be reused
without leaking the production I/O boundary.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.plan._validation import (
    PlanArtifactValidationError,
    validate_plan_section,
)

if TYPE_CHECKING:
    from ralph.mcp.artifacts.plan._section_models import PlanArtifactDict


def _coerce_positive_int(value: object) -> int | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int) and value > 0:
        return value
    if isinstance(value, str):
        stripped = value.strip()
        if stripped.isdigit() and int(stripped) > 0:
            return int(stripped)
    return None


def _jsonish(value: object) -> str:
    try:
        return json.dumps(value, ensure_ascii=True, sort_keys=True)
    except (TypeError, ValueError):
        return str(value)


def _wrap_invalid_step_entry(value: object, index: int) -> dict[str, object]:
    return {
        "number": index,
        "title": f"Unresolved staged step {index}",
        "content": _jsonish(value),
        "step_type": "action",
        "raw_step_json": value,
    }


def _steps_from_sections(sections: PlanArtifactDict) -> tuple[list[dict[str, object]], list[str]]:
    raw_steps = sections.get("steps")
    if raw_steps is None:
        return [], []
    if not isinstance(raw_steps, list):
        raise PlanArtifactValidationError("section 'steps' must be a JSON array")

    steps: list[dict[str, object]] = []
    warnings: list[str] = []
    raw_step_items = cast("list[object]", raw_steps)
    for index, step in enumerate(raw_step_items, start=1):
        if isinstance(step, dict):
            steps.append(dict(cast("dict[str, object]", step)))
            continue
        steps.append(_wrap_invalid_step_entry(step, index))
        warnings.append(
            f"step at index {index} was valid JSON but not a JSON object; preserved "
            "it in raw_step_json for validate_draft/finalize to report"
        )
    return steps, warnings


def _collect_ac_ids(sections: PlanArtifactDict) -> set[str]:
    """Return the set of AC ids declared in ``design.acceptance_criteria.criteria``."""
    design = sections.get("design")
    if not isinstance(design, dict):
        return set()
    ac = design.get("acceptance_criteria")
    if not isinstance(ac, dict):
        return set()
    criteria = ac.get("criteria")
    if not isinstance(criteria, list):
        return set()
    return {
        cast("str", entry["id"])
        for entry in criteria
        if isinstance(entry, dict) and isinstance(entry.get("id"), str)
    }


def _remap_ac_step_refs(
    sections: PlanArtifactDict,
    number_map: dict[int, int],
) -> tuple[PlanArtifactDict, list[str], list[str], list[str]]:
    """Keep ``AC.satisfied_by_steps`` in lockstep with the new step numbering.

    Returns the new sections dict plus two parallel lists:

    - ``rewritten_ac_ids``: AC ids whose ``satisfied_by_steps`` list had at
      least one entry translated through ``number_map`` (the references
      survived the reindex but moved).
    - ``dropped_ac_ids``: AC ids whose ``satisfied_by_steps`` list lost at
      least one entry because the referenced step is gone. This remains
      empty for the lenient staging path because unresolved references are
      preserved as JSON objects for validate/finalize to reject.

    Entries that survive the reindex are translated through ``number_map``.
    Entries that no longer point at a real step are preserved as unresolved
    JSON markers so a draft edit does not silently lose user intent.
    """
    new_sections: dict[str, object] = dict(sections)
    design = new_sections.get("design")
    if not isinstance(design, dict):
        return new_sections, [], [], []
    ac = design.get("acceptance_criteria")
    if not isinstance(ac, dict):
        return new_sections, [], [], []
    criteria = ac.get("criteria")
    if not isinstance(criteria, list):
        return new_sections, [], [], []
    new_criteria: list[object] = []
    rewritten_ac_ids: list[str] = []
    dropped_ac_ids: list[str] = []
    warnings: list[str] = []
    for entry in criteria:
        if not isinstance(entry, dict):
            new_criteria.append(entry)
            continue
        refs = entry.get("satisfied_by_steps")
        if not isinstance(refs, list):
            if refs is not None:
                warnings.append(
                    "AC satisfied_by_steps was not a list; left unchanged for "
                    "validate_draft/finalize to report"
                )
            new_criteria.append(entry)
            continue
        remapped: list[object] = []
        rewritten_for_this_ac = False
        kept_non_int: list[object] = []
        for ref in refs:
            ref_number = _coerce_positive_int(ref)
            if ref_number is None:
                kept_non_int.append(ref)
                warnings.append(
                    "AC satisfied_by_steps contained a non-integer entry; left it "
                    "unchanged for validate_draft/finalize to report"
                )
                continue
            new_number = number_map.get(ref_number)
            if new_number is None:
                remapped.append(ref_number)
                warnings.append(
                    "AC satisfied_by_steps references a step that is not currently staged; "
                    "preserved the numeric reference so a later step edit can satisfy it"
                )
                continue
            if new_number != ref_number:
                rewritten_for_this_ac = True
            remapped.append(new_number)
        remapped.extend(kept_non_int)
        new_entry: dict[str, object] = dict(entry)
        new_entry["satisfied_by_steps"] = remapped
        new_criteria.append(new_entry)
        ac_id = new_entry.get("id")
        if not isinstance(ac_id, str):
            continue
        if rewritten_for_this_ac and ac_id not in rewritten_ac_ids:
            rewritten_ac_ids.append(ac_id)
    new_ac_dict: dict[str, object] = dict(ac)
    new_ac_dict["criteria"] = new_criteria
    new_design_dict: dict[str, object] = dict(design)
    new_design_dict["acceptance_criteria"] = new_ac_dict
    new_sections["design"] = new_design_dict
    return new_sections, rewritten_ac_ids, dropped_ac_ids, warnings


def _normalize_step_edit_payload(
    step_payload: object,
    *,
    synthetic_number: int,
) -> dict[str, object]:
    if not isinstance(step_payload, dict):
        raise PlanArtifactValidationError("step payload must be a JSON object")
    payload: dict[str, object] = dict(cast("dict[str, object]", step_payload))
    payload["number"] = synthetic_number
    return _normalize_lenient_step_fields(payload)


def _normalize_lenient_step_fields(step: dict[str, object]) -> dict[str, object]:
    normalized = dict(step)
    for field in ("targets", "depends_on", "satisfies", "expected_evidence"):
        value = normalized.get(field)
        if value is None or isinstance(value, list):
            continue
        if field == "depends_on" and _coerce_positive_int(value) is not None:
            normalized[field] = [_coerce_positive_int(value)]
            continue
        if field in {"satisfies", "expected_evidence"} and isinstance(value, str):
            stripped = value.strip()
            normalized[field] = [stripped] if stripped else []
            continue
        if isinstance(value, dict) and field in {"targets", "expected_evidence"}:
            normalized[field] = [value]
    expected_evidence = normalized.get("expected_evidence")
    if isinstance(expected_evidence, list):
        normalized_evidence: list[object] = []
        for entry in cast("list[object]", expected_evidence):
            if isinstance(entry, str):
                stripped = entry.strip()
                if stripped:
                    normalized_evidence.append({"kind": "file", "ref": stripped})
                continue
            normalized_evidence.append(entry)
        normalized["expected_evidence"] = normalized_evidence
    return normalized


def _prepare_steps_for_reindex(
    steps: list[dict[str, object]],
) -> tuple[list[dict[str, object]], list[int], list[str]]:
    valid_numbers = [
        number
        for step in steps
        if (number := _coerce_positive_int(step.get("number"))) is not None
    ]
    next_synthetic = max(valid_numbers, default=0) + 1
    seen: set[int] = set()
    prepared_steps: list[dict[str, object]] = []
    old_numbers: list[int] = []
    warnings: list[str] = []
    for position, step in enumerate(steps, start=1):
        raw_number = step.get("number")
        number = _coerce_positive_int(raw_number)
        if number is None:
            number = next_synthetic
            next_synthetic += 1
            warnings.append(
                f"step at index {position} had missing or invalid number; assigned "
                "a temporary number before reindexing"
            )
        elif number in seen:
            number = next_synthetic
            next_synthetic += 1
            warnings.append(
                f"step at index {position} reused a duplicate number; assigned "
                "a temporary number before reindexing"
            )
        seen.add(number)
        normalized = _normalize_lenient_step_fields(dict(step))
        normalized["number"] = number
        prepared_steps.append(normalized)
        old_numbers.append(number)
    return prepared_steps, old_numbers, warnings


def _steps_schema_warnings(steps: list[dict[str, object]]) -> list[str]:
    try:
        validate_plan_section("steps", steps, mode="replace")
    except PlanArtifactValidationError as exc:
        return [
            "staged steps do not yet pass PlanStep validation; run "
            f"ralph_validate_draft before finalize: {exc}"
        ]
    return []


def _reindex_plan_steps(
    steps: list[dict[str, object]],
    *,
    removed_step_number: int | None = None,
) -> tuple[list[dict[str, object]], dict[int, int], list[int], list[str]]:
    """Reindex the steps list to 1-based contiguous numbers.

    Returns the reindexed steps, the ``{old_number: new_number}`` map, and
    the list of step numbers whose ``depends_on`` array was rewritten.
    """
    prepared_steps, old_numbers, warnings = _prepare_steps_for_reindex(steps)
    number_map = {old_number: index for index, old_number in enumerate(old_numbers, start=1)}
    updated_steps: list[dict[str, object]] = []
    rewritten_depends_on: list[int] = []
    for step, old_number in zip(prepared_steps, old_numbers, strict=False):
        remapped = dict(step)
        remapped["number"] = number_map[old_number]
        raw_dependencies = step.get("depends_on", [])
        if raw_dependencies is None:
            raw_dependencies = []
        if not isinstance(raw_dependencies, list):
            warnings.append(
                f"step {old_number} depends_on is not a list; left it unchanged for "
                "validate_draft/finalize to report"
            )
            remapped["depends_on"] = raw_dependencies
            updated_steps.append(remapped)
            continue

        remapped_dependencies: list[object] = []
        step_depends_rewritten = False
        for dependency in raw_dependencies:
            dependency_number = _coerce_positive_int(dependency)
            if dependency_number is None:
                warnings.append(
                    f"step {old_number} depends_on contains a non-integer entry; "
                    "left it unchanged for validate_draft/finalize to report"
                )
                remapped_dependencies.append(dependency)
                continue
            if removed_step_number is not None and dependency_number == removed_step_number:
                warnings.append(
                    f"step {old_number} depended on removed step {removed_step_number}; "
                    "preserved it as unresolved JSON for validate_draft/finalize to report"
                )
                remapped_dependencies.append({"removed_step_number": removed_step_number})
                step_depends_rewritten = True
                continue
            mapped_dependency = number_map.get(dependency_number)
            if mapped_dependency is None:
                warnings.append(
                    f"step {old_number} depends_on references unknown step "
                    f"{dependency_number}; preserved the numeric reference so a later "
                    "step edit can satisfy it"
                )
                remapped_dependencies.append(dependency_number)
                continue
            if mapped_dependency != dependency_number:
                step_depends_rewritten = True
            remapped_dependencies.append(mapped_dependency)
        remapped["depends_on"] = remapped_dependencies
        if step_depends_rewritten and remapped["number"] not in rewritten_depends_on:
            rewritten_depends_on.append(cast("int", remapped["number"]))
        updated_steps.append(remapped)

    warnings.extend(_steps_schema_warnings(updated_steps))
    return updated_steps, number_map, rewritten_depends_on, warnings


def _build_step_mutation_echo(
    *,
    action: str,
    new_step_number: int | None,
    step_number: int | None,
    removed_step_number: int | None,
    from_step_number: int | None,
    to_index: int | None,
    number_map: dict[int, int],
    rewritten_depends_on: list[int],
    rewritten_ac_satisfied_by_steps: list[str],
    dropped_ac_satisfied_by_steps: list[str],
    total_steps: int,
    validation_warnings: list[str],
) -> dict[str, object]:
    """Build the JSON echo payload the 4 step-mutation tools return.

    The shape is documented in the tool specs in
    ``ralph/mcp/tools/bridge/_specs_artifacts.py`` and in
    ``.agent/artifact-formats/plan.md`` §'Step-mutation read-after-write echo'.
    The action-specific fields (``new_step_number``, ``step_number``,
    ``removed_step_number``, ``from_step_number``, ``to_index``) are
    populated per the action. The common fields (``reindex_map``,
    ``rewritten_depends_on``, ``rewritten_ac_satisfied_by_steps``,
    ``dropped_ac_satisfied_by_steps``, ``total_steps``) are always
    populated.
    """
    deduped_warnings: list[str] = []
    for warning in validation_warnings:
        if warning not in deduped_warnings:
            deduped_warnings.append(warning)
    echo: dict[str, object] = {
        "action": action,
        "reindex_map": dict(number_map),
        "rewritten_depends_on": list(rewritten_depends_on),
        "rewritten_ac_satisfied_by_steps": list(rewritten_ac_satisfied_by_steps),
        "dropped_ac_satisfied_by_steps": list(dropped_ac_satisfied_by_steps),
        "total_steps": total_steps,
        "validation_warnings": deduped_warnings,
    }
    if new_step_number is not None:
        echo["new_step_number"] = new_step_number
    if step_number is not None:
        echo["step_number"] = step_number
    if removed_step_number is not None:
        echo["removed_step_number"] = removed_step_number
    if from_step_number is not None:
        echo["from_step_number"] = from_step_number
    if to_index is not None:
        echo["to_index"] = to_index
    return echo


def _apply_step_mutation(
    sections: PlanArtifactDict,
    *,
    steps: list[dict[str, object]],
    initial_warnings: list[str] | None = None,
) -> tuple[PlanArtifactDict, dict[int, int], list[int], list[str], list[str], list[str]]:
    """Run the reindex + AC remap and return the new sections + echo lists.

    Helper for the four public entry points. The reindex produces a
    number map and a list of step numbers whose ``depends_on`` was
    rewritten. The AC remap produces a list of AC ids whose
    ``satisfied_by_steps`` was rewritten AND a list of AC ids whose
    ``satisfied_by_steps`` entries were dropped (orphan references).
    """
    reindexed_steps, number_map, rewritten_depends_on, warnings = _reindex_plan_steps(steps)
    if initial_warnings:
        warnings = [*initial_warnings, *warnings]
    updated_sections: dict[str, object] = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections, rewritten_ac_ids, dropped_ac_ids, ac_warnings = _remap_ac_step_refs(
        updated_sections,
        number_map,
    )
    warnings.extend(ac_warnings)
    return (
        updated_sections,
        number_map,
        rewritten_depends_on,
        rewritten_ac_ids,
        dropped_ac_ids,
        warnings,
    )


def _clamp_insert_index(index: int, steps_count: int) -> int:
    """Normalize a requested 1-based insertion point.

    Agents often use this tool as an append primitive by passing a large
    number, or as a prepend primitive by passing zero/negative numbers. The
    insert operation is intentionally tolerant: anything before the first slot
    becomes ``1`` and anything past the append slot becomes ``len + 1``.
    """
    return max(1, min(int(index), steps_count + 1))


def insert_plan_step(
    sections: PlanArtifactDict,
    *,
    index: int,
    step_payload: object,
) -> PlanArtifactDict:
    """Insert a new step into a staged plan draft.

    Args:
        sections: The current staged plan sections dict.
        index: 1-based insertion position. Values below 1 prepend; values
            past ``len(steps) + 1`` append.
        step_payload: JSON object describing the new step. ``number`` is
            ignored and replaced with a synthetic number that is then
            reindexed.

    Returns:
        A new ``sections`` dict with the step inserted and all steps
        reindexed to contiguous 1-based numbers.

    Raises:
        PlanArtifactValidationError: If ``sections`` is malformed or the
            payload is not a JSON object.
    """
    steps, warnings = _steps_from_sections(sections)
    clamped_index = _clamp_insert_index(index, len(steps))

    existing_numbers = [
        number
        for step in steps
        if (number := _coerce_positive_int(step.get("number"))) is not None
    ]
    inserted_step = _normalize_step_edit_payload(
        step_payload,
        synthetic_number=max(existing_numbers, default=0) + 1,
    )
    steps.insert(clamped_index - 1, inserted_step)
    (
        updated_sections,
        _number_map,
        _rewritten_depends_on,
        _rewritten_ac,
        _dropped_ac,
        _warnings,
    ) = _apply_step_mutation(sections, steps=steps, initial_warnings=warnings)
    return updated_sections


def insert_plan_step_with_echo(
    sections: PlanArtifactDict,
    *,
    index: int,
    step_payload: object,
) -> tuple[PlanArtifactDict, dict[str, object]]:
    """Insert a step AND build the echo payload in one call.

    Returns the new sections dict and the echo dict the tool handler
    serializes to the agent. The echo shape is documented in
    ``_build_step_mutation_echo``.
    """
    steps, source_warnings = _steps_from_sections(sections)
    clamped_index = _clamp_insert_index(index, len(steps))

    existing_numbers = [
        number
        for step in steps
        if (number := _coerce_positive_int(step.get("number"))) is not None
    ]
    inserted_step = _normalize_step_edit_payload(
        step_payload,
        synthetic_number=max(existing_numbers, default=0) + 1,
    )
    synthetic_number = cast("int", inserted_step["number"])
    steps.insert(clamped_index - 1, inserted_step)
    (
        updated_sections,
        number_map,
        rewritten_depends_on,
        rewritten_ac,
        dropped_ac,
        warnings,
    ) = _apply_step_mutation(sections, steps=steps, initial_warnings=source_warnings)
    reindexed_steps = cast("list[dict[str, object]]", updated_sections.get("steps", []))
    echo = _build_step_mutation_echo(
        action="insert",
        new_step_number=number_map[synthetic_number],
        step_number=None,
        removed_step_number=None,
        from_step_number=None,
        to_index=None,
        number_map=number_map,
        rewritten_depends_on=rewritten_depends_on,
        rewritten_ac_satisfied_by_steps=rewritten_ac,
        dropped_ac_satisfied_by_steps=dropped_ac,
        total_steps=len(reindexed_steps),
        validation_warnings=warnings,
    )
    echo["index"] = clamped_index
    return updated_sections, echo


def replace_plan_step(
    sections: PlanArtifactDict,
    *,
    step_number: int,
    step_payload: object,
) -> PlanArtifactDict:
    """Replace an existing step in a staged plan draft.

    Args:
        sections: The current staged plan sections dict.
        step_number: The 1-based step number to replace.
        step_payload: JSON object describing the replacement step.

    Returns:
        A new ``sections`` dict with the step replaced and all steps
        reindexed to contiguous 1-based numbers.

    Raises:
        PlanArtifactValidationError: If the step does not exist or the
            payload is not a JSON object.
    """
    steps, warnings = _steps_from_sections(sections)
    target_index = next(
        (
            idx
            for idx, step in enumerate(steps)
            if _coerce_positive_int(step.get("number")) == step_number
        ),
        None,
    )
    if target_index is None:
        raise PlanArtifactValidationError(f"step {step_number} does not exist")

    replacement_step = _normalize_step_edit_payload(
        step_payload,
        synthetic_number=step_number,
    )
    steps[target_index] = replacement_step
    (
        updated_sections,
        _number_map,
        _rewritten_depends_on,
        _rewritten_ac,
        _dropped_ac,
        _warnings,
    ) = _apply_step_mutation(sections, steps=steps, initial_warnings=warnings)
    return updated_sections


def replace_plan_step_with_echo(
    sections: PlanArtifactDict,
    *,
    step_number: int,
    step_payload: object,
) -> tuple[PlanArtifactDict, dict[str, object]]:
    """Replace a step AND build the echo payload in one call.

    The echo shape is documented in ``_build_step_mutation_echo``.
    """
    steps, source_warnings = _steps_from_sections(sections)
    target_index = next(
        (
            idx
            for idx, step in enumerate(steps)
            if _coerce_positive_int(step.get("number")) == step_number
        ),
        None,
    )
    if target_index is None:
        raise PlanArtifactValidationError(f"step {step_number} does not exist")

    replacement_step = _normalize_step_edit_payload(
        step_payload,
        synthetic_number=step_number,
    )
    steps[target_index] = replacement_step
    (
        updated_sections,
        number_map,
        rewritten_depends_on,
        rewritten_ac,
        dropped_ac,
        warnings,
    ) = _apply_step_mutation(sections, steps=steps, initial_warnings=source_warnings)
    reindexed_steps = cast("list[dict[str, object]]", updated_sections.get("steps", []))
    echo = _build_step_mutation_echo(
        action="replace",
        new_step_number=None,
        step_number=step_number,
        removed_step_number=None,
        from_step_number=None,
        to_index=None,
        number_map=number_map,
        rewritten_depends_on=rewritten_depends_on,
        rewritten_ac_satisfied_by_steps=rewritten_ac,
        dropped_ac_satisfied_by_steps=dropped_ac,
        total_steps=len(reindexed_steps),
        validation_warnings=warnings,
    )
    return updated_sections, echo


def remove_plan_step(
    sections: PlanArtifactDict,
    *,
    step_number: int,
) -> PlanArtifactDict:
    """Remove an existing step from a staged plan draft.

    Args:
        sections: The current staged plan sections dict.
        step_number: The 1-based step number to remove.

    Returns:
        A new ``sections`` dict with the step removed and all steps
        reindexed to contiguous 1-based numbers.

    Raises:
        PlanArtifactValidationError: If the step does not exist.
    """
    steps, _source_warnings = _steps_from_sections(sections)
    remaining_steps = [
        step for step in steps if _coerce_positive_int(step.get("number")) != step_number
    ]
    if len(remaining_steps) == len(steps):
        raise PlanArtifactValidationError(f"step {step_number} does not exist")

    reindexed_steps, number_map, _rewritten_depends_on, _warnings = _reindex_plan_steps(
        remaining_steps,
        removed_step_number=step_number,
    )
    updated_sections = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections, _rewritten_ac, _dropped_ac, _ac_warnings = _remap_ac_step_refs(
        updated_sections,
        number_map,
    )
    return updated_sections


def remove_plan_step_with_echo(
    sections: PlanArtifactDict,
    *,
    step_number: int,
) -> tuple[PlanArtifactDict, dict[str, object]]:
    """Remove a step AND build the echo payload in one call.

    The echo shape is documented in ``_build_step_mutation_echo``.
    """
    steps, source_warnings = _steps_from_sections(sections)
    remaining_steps = [
        step for step in steps if _coerce_positive_int(step.get("number")) != step_number
    ]
    if len(remaining_steps) == len(steps):
        raise PlanArtifactValidationError(f"step {step_number} does not exist")

    reindexed_steps, number_map, rewritten_depends_on, warnings = _reindex_plan_steps(
        remaining_steps,
        removed_step_number=step_number,
    )
    updated_sections = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections, rewritten_ac, dropped_ac, ac_warnings = _remap_ac_step_refs(
        updated_sections,
        number_map,
    )
    warnings = [*source_warnings, *warnings, *ac_warnings]
    echo = _build_step_mutation_echo(
        action="remove",
        new_step_number=None,
        step_number=None,
        removed_step_number=step_number,
        from_step_number=None,
        to_index=None,
        number_map=number_map,
        rewritten_depends_on=rewritten_depends_on,
        rewritten_ac_satisfied_by_steps=rewritten_ac,
        dropped_ac_satisfied_by_steps=dropped_ac,
        total_steps=len(reindexed_steps),
        validation_warnings=warnings,
    )
    return updated_sections, echo


def move_plan_step(
    sections: PlanArtifactDict,
    *,
    from_step_number: int,
    to_index: int,
) -> PlanArtifactDict:
    """Move a plan step to a new position in a single call.

    ``from_step_number`` identifies the source step by its current
    ``number`` field. ``to_index`` is 1-based; a value of ``1`` moves
    the step to the front of the list, and a value of ``len(steps) + 1``
    appends it to the end. Out-of-range indices are clamped to the
    valid range so the call is total.

    The function reuses the same ``_reindex_plan_steps`` and
    ``_remap_ac_step_refs`` helpers that ``insert_plan_step``,
    ``replace_plan_step``, and ``remove_plan_step`` use, so the four
    step-mutation tools share one proven reindex/depends_on/AC-remap
    implementation. The function is a hand-rolled single-reindex
    implementation (not a thin wrapper around
    ``remove_plan_step`` + ``insert_plan_step``) so the reindex is
    performed exactly once per call.
    """
    steps, _source_warnings = _steps_from_sections(sections)
    source_idx = next(
        (
            idx
            for idx, step in enumerate(steps)
            if _coerce_positive_int(step.get("number")) == from_step_number
        ),
        None,
    )
    if source_idx is None:
        raise PlanArtifactValidationError(f"step {from_step_number} does not exist")

    moved_step = steps[source_idx]
    remaining_steps = [
        step for step in steps if _coerce_positive_int(step.get("number")) != from_step_number
    ]
    upper = len(remaining_steps) + 1
    clamped_index = max(1, min(int(to_index), upper))
    new_steps = list(remaining_steps)
    new_steps.insert(clamped_index - 1, moved_step)

    reindexed_steps, number_map, _rewritten_depends_on, _warnings = _reindex_plan_steps(
        new_steps
    )
    updated_sections: PlanArtifactDict = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections, _rewritten_ac, _dropped_ac, _ac_warnings = _remap_ac_step_refs(
        updated_sections,
        number_map,
    )
    return updated_sections


def move_plan_step_with_echo(
    sections: PlanArtifactDict,
    *,
    from_step_number: int,
    to_index: int,
) -> tuple[PlanArtifactDict, dict[str, object]]:
    """Move a step AND build the echo payload in one call.

    The echo shape is documented in ``_build_step_mutation_echo``.
    """
    steps, source_warnings = _steps_from_sections(sections)
    source_idx = next(
        (
            idx
            for idx, step in enumerate(steps)
            if _coerce_positive_int(step.get("number")) == from_step_number
        ),
        None,
    )
    if source_idx is None:
        raise PlanArtifactValidationError(f"step {from_step_number} does not exist")

    moved_step = steps[source_idx]
    remaining_steps = [
        step for step in steps if _coerce_positive_int(step.get("number")) != from_step_number
    ]
    upper = len(remaining_steps) + 1
    clamped_index = max(1, min(int(to_index), upper))
    new_steps = list(remaining_steps)
    new_steps.insert(clamped_index - 1, moved_step)

    reindexed_steps, number_map, rewritten_depends_on, warnings = _reindex_plan_steps(
        new_steps
    )
    updated_sections = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections, rewritten_ac, dropped_ac, ac_warnings = _remap_ac_step_refs(
        updated_sections,
        number_map,
    )
    warnings = [*source_warnings, *warnings, *ac_warnings]
    echo = _build_step_mutation_echo(
        action="move",
        new_step_number=None,
        step_number=None,
        removed_step_number=None,
        from_step_number=from_step_number,
        to_index=clamped_index,
        number_map=number_map,
        rewritten_depends_on=rewritten_depends_on,
        rewritten_ac_satisfied_by_steps=rewritten_ac,
        dropped_ac_satisfied_by_steps=dropped_ac,
        total_steps=len(reindexed_steps),
        validation_warnings=warnings,
    )
    return updated_sections, echo


__all__ = [
    "insert_plan_step",
    "insert_plan_step_with_echo",
    "move_plan_step",
    "move_plan_step_with_echo",
    "remove_plan_step",
    "remove_plan_step_with_echo",
    "replace_plan_step",
    "replace_plan_step_with_echo",
]

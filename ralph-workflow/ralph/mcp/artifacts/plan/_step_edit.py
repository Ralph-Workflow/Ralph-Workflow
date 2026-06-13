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
) -> tuple[PlanArtifactDict, list[str], list[str]]:
    """Keep ``AC.satisfied_by_steps`` in lockstep with the new step numbering.

    Returns the new sections dict plus two parallel lists:

    - ``rewritten_ac_ids``: AC ids whose ``satisfied_by_steps`` list had at
      least one entry translated through ``number_map`` (the references
      survived the reindex but moved).
    - ``dropped_ac_ids``: AC ids whose ``satisfied_by_steps`` list lost at
      least one entry because the referenced step is gone (the
      principle-of-least-surprise orphan-drop behavior).

    Entries that no longer point at a real step are dropped silently; entries
    that survive the reindex are translated through ``number_map``.
    """
    new_sections: dict[str, object] = dict(sections)
    design = new_sections.get("design")
    if not isinstance(design, dict):
        return new_sections, [], []
    ac = design.get("acceptance_criteria")
    if not isinstance(ac, dict):
        return new_sections, [], []
    criteria = ac.get("criteria")
    if not isinstance(criteria, list):
        return new_sections, [], []
    kept_new_numbers = set(number_map.values())
    new_criteria: list[object] = []
    rewritten_ac_ids: list[str] = []
    dropped_ac_ids: list[str] = []
    for entry in criteria:
        if not isinstance(entry, dict):
            new_criteria.append(entry)
            continue
        refs = entry.get("satisfied_by_steps")
        if not isinstance(refs, list):
            new_criteria.append(entry)
            continue
        remapped: list[int] = []
        rewritten_for_this_ac = False
        dropped_for_this_ac = False
        for ref in refs:
            if not isinstance(ref, int) or ref not in number_map:
                dropped_for_this_ac = True
                continue
            new_number = number_map[ref]
            if new_number not in kept_new_numbers:
                dropped_for_this_ac = True
                continue
            if new_number != ref:
                rewritten_for_this_ac = True
            remapped.append(new_number)
        new_entry: dict[str, object] = dict(entry)
        new_entry["satisfied_by_steps"] = remapped
        new_criteria.append(new_entry)
        ac_id = new_entry.get("id")
        if not isinstance(ac_id, str):
            continue
        if rewritten_for_this_ac and ac_id not in rewritten_ac_ids:
            rewritten_ac_ids.append(ac_id)
        if dropped_for_this_ac and ac_id not in dropped_ac_ids:
            dropped_ac_ids.append(ac_id)
    new_ac_dict: dict[str, object] = dict(ac)
    new_ac_dict["criteria"] = new_criteria
    new_design_dict: dict[str, object] = dict(design)
    new_design_dict["acceptance_criteria"] = new_ac_dict
    new_sections["design"] = new_design_dict
    return new_sections, rewritten_ac_ids, dropped_ac_ids


def _normalize_step_edit_payload(
    step_payload: object,
    *,
    synthetic_number: int,
) -> dict[str, object]:
    if not isinstance(step_payload, dict):
        raise PlanArtifactValidationError("step payload must be a JSON object")
    payload: dict[str, object] = dict(cast("dict[str, object]", step_payload))
    payload["number"] = synthetic_number
    fragment = validate_plan_section("steps", payload, mode="append")
    return cast("dict[str, object]", fragment)


def _reindex_plan_steps(
    steps: list[dict[str, object]],
    *,
    removed_step_number: int | None = None,
) -> tuple[list[dict[str, object]], dict[int, int], list[int]]:
    """Reindex the steps list to 1-based contiguous numbers.

    Returns the reindexed steps, the ``{old_number: new_number}`` map, and
    the list of step numbers whose ``depends_on`` array was rewritten.
    """
    old_numbers = [cast("int", step["number"]) for step in steps]
    if len(set(old_numbers)) != len(old_numbers):
        raise PlanArtifactValidationError("plan steps must have unique step numbers before edit")

    number_map = {old_number: index for index, old_number in enumerate(old_numbers, start=1)}
    updated_steps: list[dict[str, object]] = []
    rewritten_depends_on: list[int] = []
    for step, old_number in zip(steps, old_numbers, strict=False):
        remapped = dict(step)
        remapped["number"] = number_map[old_number]
        dependencies = step.get("depends_on", [])
        if not isinstance(dependencies, list):
            raise PlanArtifactValidationError("step depends_on must be a JSON array")

        remapped_dependencies: list[int] = []
        step_depends_rewritten = False
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
            if mapped_dependency != dependency:
                step_depends_rewritten = True
            remapped_dependencies.append(mapped_dependency)
        remapped["depends_on"] = remapped_dependencies
        if step_depends_rewritten and remapped["number"] not in rewritten_depends_on:
            rewritten_depends_on.append(cast("int", remapped["number"]))
        updated_steps.append(remapped)

    normalized = validate_plan_section("steps", updated_steps, mode="replace")
    return cast("list[dict[str, object]]", normalized), number_map, rewritten_depends_on


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
    echo: dict[str, object] = {
        "action": action,
        "reindex_map": dict(number_map),
        "rewritten_depends_on": list(rewritten_depends_on),
        "rewritten_ac_satisfied_by_steps": list(rewritten_ac_satisfied_by_steps),
        "dropped_ac_satisfied_by_steps": list(dropped_ac_satisfied_by_steps),
        "total_steps": total_steps,
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
) -> tuple[PlanArtifactDict, list[int], list[str], list[str]]:
    """Run the reindex + AC remap and return the new sections + echo lists.

    Helper for the four public entry points. The reindex produces a
    number map and a list of step numbers whose ``depends_on`` was
    rewritten. The AC remap produces a list of AC ids whose
    ``satisfied_by_steps`` was rewritten AND a list of AC ids whose
    ``satisfied_by_steps`` entries were dropped (orphan references).
    """
    reindexed_steps, number_map, rewritten_depends_on = _reindex_plan_steps(steps)
    updated_sections: dict[str, object] = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections, rewritten_ac_ids, dropped_ac_ids = _remap_ac_step_refs(
        updated_sections,
        number_map,
    )
    return updated_sections, rewritten_depends_on, rewritten_ac_ids, dropped_ac_ids


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
    updated_sections, _rewritten_depends_on, _rewritten_ac, _dropped_ac = _apply_step_mutation(
        sections, steps=steps
    )
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
    updated_sections, rewritten_depends_on, rewritten_ac, dropped_ac = _apply_step_mutation(
        sections, steps=steps
    )
    reindexed_steps = cast("list[dict[str, object]]", updated_sections.get("steps", []))
    number_map = {
        cast("int", step["number"]): index_
        for index_, step in enumerate(reindexed_steps, start=1)
    }
    echo = _build_step_mutation_echo(
        action="insert",
        new_step_number=cast("int", inserted_step["number"]),
        step_number=None,
        removed_step_number=None,
        from_step_number=None,
        to_index=None,
        number_map=number_map,
        rewritten_depends_on=rewritten_depends_on,
        rewritten_ac_satisfied_by_steps=rewritten_ac,
        dropped_ac_satisfied_by_steps=dropped_ac,
        total_steps=len(reindexed_steps),
    )
    return updated_sections, echo


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
    updated_sections, _rewritten_depends_on, _rewritten_ac, _dropped_ac = _apply_step_mutation(
        sections, steps=steps
    )
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
    updated_sections, rewritten_depends_on, rewritten_ac, dropped_ac = _apply_step_mutation(
        sections, steps=steps
    )
    reindexed_steps = cast("list[dict[str, object]]", updated_sections.get("steps", []))
    number_map = {
        cast("int", step["number"]): index_
        for index_, step in enumerate(reindexed_steps, start=1)
    }
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
    )
    return updated_sections, echo


def remove_plan_step(
    sections: PlanArtifactDict,
    *,
    step_number: int,
) -> PlanArtifactDict:
    steps = _steps_from_sections(sections)
    remaining_steps = [step for step in steps if step["number"] != step_number]
    if len(remaining_steps) == len(steps):
        raise PlanArtifactValidationError(f"step {step_number} does not exist")

    reindexed_steps, number_map, _rewritten_depends_on = _reindex_plan_steps(
        remaining_steps,
        removed_step_number=step_number,
    )
    updated_sections = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections, _rewritten_ac, _dropped_ac = _remap_ac_step_refs(
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
    steps = _steps_from_sections(sections)
    remaining_steps = [step for step in steps if step["number"] != step_number]
    if len(remaining_steps) == len(steps):
        raise PlanArtifactValidationError(f"step {step_number} does not exist")

    reindexed_steps, number_map, rewritten_depends_on = _reindex_plan_steps(
        remaining_steps,
        removed_step_number=step_number,
    )
    updated_sections = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections, rewritten_ac, dropped_ac = _remap_ac_step_refs(
        updated_sections,
        number_map,
    )
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
    steps = _steps_from_sections(sections)
    source_idx = next(
        (idx for idx, step in enumerate(steps) if step["number"] == from_step_number),
        None,
    )
    if source_idx is None:
        raise PlanArtifactValidationError(f"step {from_step_number} does not exist")

    moved_step = steps[source_idx]
    remaining_steps = [step for step in steps if step["number"] != from_step_number]
    upper = len(remaining_steps) + 1
    clamped_index = max(1, min(int(to_index), upper))
    new_steps = list(remaining_steps)
    new_steps.insert(clamped_index - 1, moved_step)

    reindexed_steps, number_map, _rewritten_depends_on = _reindex_plan_steps(new_steps)
    updated_sections: PlanArtifactDict = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections, _rewritten_ac, _dropped_ac = _remap_ac_step_refs(
        updated_sections, number_map
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
    steps = _steps_from_sections(sections)
    source_idx = next(
        (idx for idx, step in enumerate(steps) if step["number"] == from_step_number),
        None,
    )
    if source_idx is None:
        raise PlanArtifactValidationError(f"step {from_step_number} does not exist")

    moved_step = steps[source_idx]
    remaining_steps = [step for step in steps if step["number"] != from_step_number]
    upper = len(remaining_steps) + 1
    clamped_index = max(1, min(int(to_index), upper))
    new_steps = list(remaining_steps)
    new_steps.insert(clamped_index - 1, moved_step)

    reindexed_steps, number_map, rewritten_depends_on = _reindex_plan_steps(new_steps)
    updated_sections = dict(sections)
    updated_sections["steps"] = reindexed_steps
    updated_sections, rewritten_ac, dropped_ac = _remap_ac_step_refs(
        updated_sections, number_map
    )
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

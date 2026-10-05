"""Best-effort plan ownership extraction and safe dispatch selection."""

from __future__ import annotations

import re
from graphlib import CycleError, TopologicalSorter
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, TypeGuard

from pydantic import ConfigDict, Field

from ralph.pipeline.work_unit import WorkUnit
from ralph.pydantic_compat import RalphBaseModel

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "WorkUnit",
    "WorkUnitsPlan",
    "canonical_plan_references",
    "dispatchable_work_units",
    "has_unextractable_work_units",
    "parse_work_units_from_artifact",
    "sanitize_ownership_paths",
]


class WorkUnitsPlan(RalphBaseModel):
    """Typed representation of work_units[] in planning artifacts."""

    model_config = ConfigDict(frozen=True)

    work_units: list[WorkUnit] = Field(default_factory=list)


def _is_list(value: object) -> TypeGuard[list[object]]:
    return isinstance(value, list)


def _is_mapping(value: object) -> TypeGuard[dict[str, object]]:
    return isinstance(value, dict) and all(isinstance(key, str) for key in value)


def _string_list(value: object) -> list[str]:
    """Return only string members from a best-effort extracted field."""
    return [member for member in value if isinstance(member, str)] if _is_list(value) else []


def sanitize_ownership_paths(paths: list[str]) -> list[str]:
    """Drop unsafe ownership rather than authorizing broad worker writes."""
    safe: list[str] = []
    for path in paths:
        parsed = PurePosixPath(path)
        if (
            not parsed.parts
            or parsed.is_absolute()
            or "\\" in path
            or any(part in {"..", ".agent", ".git", ".worktrees"} for part in parsed.parts)
        ):
            continue
        normalized = str(parsed)
        if normalized not in safe:
            safe.append(normalized)
    return safe


def _step_ref(step: Mapping[str, object]) -> str | None:
    candidate = step.get("id") or step.get("step_id")
    if not isinstance(candidate, str):
        number = step.get("number")
        candidate = f"S-{number}" if type(number) is int else ""
    return candidate if re.fullmatch(r"S-[1-9][0-9]*", candidate) else None


def canonical_plan_references(
    content: Mapping[str, object],
) -> tuple[frozenset[str], frozenset[str], frozenset[str]]:
    """Return usable steps, units, and owned existing steps for proof selection."""
    raw_steps = content.get("steps")
    steps = frozenset(
        ref
        for step in (raw_steps if _is_list(raw_steps) else [])
        if _is_mapping(step) and (ref := _step_ref(step)) is not None
    )
    parsed = parse_work_units_from_artifact(content)
    units = parsed.work_units if parsed is not None else []
    return (
        steps,
        frozenset(unit.unit_id for unit in units),
        frozenset(ref for unit in units for ref in unit.step_ids) & steps,
    )


def dispatchable_work_units(units: tuple[WorkUnit, ...]) -> tuple[WorkUnit, ...]:
    """Select only bounded scopes whose complete prerequisite graph is runnable."""
    candidates = {
        unit.unit_id: unit.model_copy(
            update={
                "allowed_directories": sanitize_ownership_paths(unit.allowed_directories),
                "paths": sanitize_ownership_paths(unit.paths),
            }
        )
        for unit in units
    }
    reachable: set[str] = set()
    while True:
        ready = {
            key
            for key, unit in candidates.items()
            if key not in reachable
            and (unit.allowed_directories or unit.paths)
            and all(dependency in reachable for dependency in unit.dependencies)
        }
        if not ready:
            break
        reachable.update(ready)
    return tuple(candidates[unit.unit_id] for unit in units if unit.unit_id in reachable)


def _has_unsafe_step_graph(artifact: Mapping[str, object]) -> bool:
    raw_steps = artifact.get("steps")
    graph: dict[str, set[str]] = {}
    for step in raw_steps if _is_list(raw_steps) else []:
        if not _is_mapping(step) or (ref := _step_ref(step)) is None:
            continue
        raw_dependencies = step.get("depends_on")
        graph[ref] = (
            {f"S-{number}" for number in raw_dependencies if type(number) is int}
            if _is_list(raw_dependencies)
            else set()
        )
    if any(
        dependency not in graph for dependencies in graph.values() for dependency in dependencies
    ):
        return True
    try:
        tuple(TopologicalSorter(graph).static_order())
    except CycleError:
        return True
    return False


def has_unextractable_work_units(artifact: Mapping[str, object]) -> bool:
    """Report whether raw plan units include any work unsafe for worker dispatch.

    Invalid or omitted unit metadata must retain the plan in the main session;
    returning true prevents partial native fan-out from discarding that work.
    """
    if artifact.get("unextractable_work_units") is True or _has_unsafe_step_graph(artifact):
        return True
    parsed = parse_work_units_from_artifact(artifact)
    parsed_ids = {unit.unit_id for unit in parsed.work_units} if parsed is not None else set()
    for raw_units in (artifact.get("work_units"), artifact.get("parallel_plan")):
        if not _is_list(raw_units):
            continue
        for item in raw_units:
            if not _is_mapping(item):
                return True
            unit_id = item.get("unit_id", item.get("id"))
            if not isinstance(unit_id, str) or unit_id not in parsed_ids:
                return True
    return False


def parse_work_units_from_artifact(artifact: Mapping[str, object]) -> WorkUnitsPlan | None:
    """Best-effort convert extracted plan units without making prose a gate.

    Invalid siblings are omitted deterministically; their raw plan prose remains
    available to the main agent rather than creating an unsafe worker scope.
    """
    raw_fields = [artifact.get("work_units"), artifact.get("parallel_plan")]
    if all(value is None for value in raw_fields):
        return None
    raw = [item for value in raw_fields if _is_list(value) for item in value]

    units: list[WorkUnit] = []
    seen: set[str] = set()
    for item in raw:
        if not _is_mapping(item):
            continue
        unit_id = item.get("unit_id", item.get("id"))
        if not isinstance(unit_id, str) or unit_id in seen or unit_id.startswith("AC-"):
            continue
        edit_area = item.get("edit_area")
        ownership = edit_area if _is_mapping(edit_area) else item
        directories: object = ownership.get("directories", ownership.get("allowed_directories", []))
        paths: object = ownership.get("paths", ownership.get("allowed_paths", []))
        files: object = item.get("files", [])
        dependencies: object = item.get("dependencies", item.get("depends_on", []))
        step_ids: object = item.get("step_ids", [])
        owned_steps = list(
            {ref: None for ref in _string_list(step_ids) if re.fullmatch(r"S-[1-9][0-9]*", ref)}
        )
        safe_directories = sanitize_ownership_paths(_string_list(directories))
        safe_paths = sanitize_ownership_paths([*_string_list(paths), *_string_list(files)])
        if not _string_list(directories) and not _string_list(paths) and not _string_list(files):
            steps = artifact.get("steps")
            if _is_list(steps):
                for step in steps:
                    if _is_mapping(step) and _step_ref(step) in owned_steps:
                        targets = step.get("targets")
                        target_paths = [
                            target_path
                            for target in (targets if _is_list(targets) else [])
                            if _is_mapping(target)
                            and isinstance(target_path := target.get("path"), str)
                        ]
                        safe_paths.extend(
                            sanitize_ownership_paths(
                                [
                                    *_string_list(step.get("files")),
                                    *target_paths,
                                ]
                            )
                        )
        try:
            unit = WorkUnit(
                unit_id=unit_id,
                description=str(item.get("description") or unit_id),
                allowed_directories=safe_directories,
                paths=sanitize_ownership_paths(safe_paths),
                dependencies=_string_list(dependencies),
                step_ids=owned_steps,
            )
        except (TypeError, ValueError):
            continue
        seen.add(unit_id)
        units.append(unit)
    return WorkUnitsPlan(work_units=units)

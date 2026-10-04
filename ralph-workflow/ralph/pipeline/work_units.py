"""Planning work_units parsing and validation.

This module provides a typed parser for work_units[] declared in planning
artifacts. It intentionally focuses on schema and graph validation; execution
fanout remains orchestrator-owned.
"""

from __future__ import annotations

from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field

from ralph.pipeline.work_unit import WorkUnit
from ralph.pipeline.work_units_validation_error import WorkUnitsValidationError
from ralph.pydantic_compat import RalphBaseModel

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = [
    "RESERVED_EDIT_PATHS",
    "WorkUnit",
    "WorkUnitsPlan",
    "WorkUnitsValidationError",
    "parse_work_units_from_artifact",
    "validate_for_same_workspace",
]

RESERVED_EDIT_PATHS: frozenset[str] = frozenset(
    {
        ".agent",
        ".git",
        # Defense-in-depth: deny .worktrees entries; not a supported edit area.
        ".worktrees",
        ".",
        "",
    }
)


class WorkUnitsPlan(RalphBaseModel):
    """Typed representation of work_units[] in planning artifacts."""

    model_config = ConfigDict(frozen=True)

    work_units: list[WorkUnit] = Field(default_factory=list)


def validate_for_same_workspace(plan: WorkUnitsPlan, *, planning_intent: bool = False) -> None:
    """Validate that a plan is safe for same-workspace parallel execution.

    Enforces rules that apply specifically when workers share the same checkout:
    - Every unit must declare at least one allowed_directory.
    - No unit may declare a reserved path (.agent, .git, .worktrees, ., "").
    - No two units may have overlapping edit areas (prefix-overlap by path segments).

    Raises:
        WorkUnitsValidationError: with a human-readable message naming the problematic
            units/paths and suggesting a fix.
    """
    for unit in plan.work_units:
        if not unit.allowed_directories and not planning_intent:
            raise WorkUnitsValidationError(
                f"Work unit '{unit.unit_id}' does not declare any allowed_directories. "
                "Each unit must declare the subdirectories it is permitted to edit. "
                "Choose disjoint subdirectories or merge the work units."
            )
        for d in unit.allowed_directories:
            _check_reserved(unit.unit_id, d)

    if not planning_intent:
        _check_no_overlap(plan.work_units)


def _check_reserved(unit_id: str, directory: str) -> None:
    p = PurePosixPath(directory)
    if not p.parts:
        raise WorkUnitsValidationError(
            f"Work unit '{unit_id}' declares an empty allowed_directory. "
            "The empty string is a reserved path. Choose a specific subdirectory."
        )
    first_part = p.parts[0] if p.parts else ""
    normalized = str(p)
    if first_part == ".worktrees":
        raise WorkUnitsValidationError(
            f"Work unit '{unit_id}' declares reserved path {directory!r} as an edit area. "
            "Path '.worktrees' is reserved (defense-in-depth) and may not be used as an "
            "allowed_directory. Choose a project-owned subdirectory."
        )
    if normalized in RESERVED_EDIT_PATHS or first_part in {".agent", ".git"}:
        raise WorkUnitsValidationError(
            f"Work unit '{unit_id}' declares reserved path {directory!r} as an edit area. "
            "Reserved paths (.agent, .git, .) may not be declared as "
            "allowed_directories. Choose a project-owned subdirectory."
        )


def _check_no_overlap(units: list[WorkUnit]) -> None:
    """Detect prefix-overlap between any two units' allowed_directories."""
    all_dirs: list[tuple[str, str, tuple[str, ...]]] = []

    def sort_key(unit: WorkUnit) -> str:
        return unit.unit_id

    for unit in sorted(units, key=sort_key):
        for d in sorted(unit.allowed_directories):
            parts = PurePosixPath(d).parts
            all_dirs.append((unit.unit_id, d, parts))

    for i, (uid1, d1, p1) in enumerate(all_dirs):
        for uid2, d2, p2 in all_dirs[i + 1 :]:
            if uid1 == uid2:
                continue
            if _path_parts_overlap(p1, p2):
                raise WorkUnitsValidationError(
                    f"Work unit '{uid1}' edit area '{d1}' overlaps with "
                    f"work unit '{uid2}' edit area '{d2}'. "
                    "Choose disjoint subdirectories or merge the work units."
                )


def _path_parts_overlap(a: tuple[str, ...], b: tuple[str, ...]) -> bool:
    """Return True only when one path is a strict prefix of the other (segment-aware).

    'src/api' and 'src/api2' do NOT overlap (different second segment).
    'src/api' and 'src/api/auth' DO overlap (a is a prefix of b).
    'src/api' and 'src/api' DO overlap (exact match).
    """
    shorter, longer = (a, b) if len(a) <= len(b) else (b, a)
    return longer[: len(shorter)] == shorter


def _string_list(value: object) -> list[str]:
    """Return only string members from a best-effort extracted field."""
    return [member for member in value if isinstance(member, str)] if isinstance(value, list) else []


def parse_work_units_from_artifact(artifact: Mapping[str, object]) -> WorkUnitsPlan | None:
    """Best-effort convert extracted plan units without making prose a gate.

    Invalid siblings are omitted deterministically; their raw plan prose remains
    available to the main agent rather than creating an unsafe worker scope.
    """
    raw = artifact.get("work_units") or artifact.get("parallel_plan")
    if raw is None:
        return None
    if not isinstance(raw, list):
        return WorkUnitsPlan()

    units: list[WorkUnit] = []
    seen: set[str] = set()
    for item in raw:
        if not isinstance(item, dict):
            continue
        unit_id = item.get("unit_id", item.get("id"))
        if not isinstance(unit_id, str) or unit_id in seen or unit_id.startswith("AC-"):
            continue
        directories: object = item.get("directories", item.get("allowed_directories", []))
        paths: object = item.get("paths", [])
        files: object = item.get("files", [])
        dependencies: object = item.get("dependencies", item.get("depends_on", []))
        step_ids: object = item.get("step_ids", [])
        try:
            unit = WorkUnit(
                unit_id=unit_id,
                description=str(item.get("description", unit_id)),
                allowed_directories=_string_list(directories),
                paths=[*_string_list(paths), *_string_list(files)],
                dependencies=_string_list(dependencies),
                step_ids=_string_list(step_ids),
            )
        except (TypeError, ValueError):
            continue
        seen.add(unit_id)
        units.append(unit)
    return WorkUnitsPlan(work_units=units)

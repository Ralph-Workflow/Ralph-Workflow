"""Planning work_units parsing and validation.

This module provides a typed parser for work_units[] declared in planning
artifacts. It intentionally focuses on schema and graph validation; execution
fanout remains orchestrator-owned.
"""

from __future__ import annotations

from collections.abc import Mapping  # noqa: TC003

from pydantic import BaseModel, ConfigDict, Field, model_validator


class WorkUnitsValidationError(ValueError):
    """Raised when a planning artifact contains invalid work_units."""


class WorkUnit(BaseModel):  # type: ignore[explicit-any]
    """Single planning work unit declaration."""

    model_config = ConfigDict(frozen=True)

    unit_id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1)
    allowed_directories: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)


class WorkUnitsPlan(BaseModel):  # type: ignore[explicit-any]
    """Typed representation of work_units[] in planning artifacts."""

    model_config = ConfigDict(frozen=True)

    work_units: list[WorkUnit] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_graph(self) -> WorkUnitsPlan:
        ids = [unit.unit_id for unit in self.work_units]
        seen: set[str] = set()
        for unit_id in ids:
            if unit_id in seen:
                raise WorkUnitsValidationError(f"Duplicate work unit id: {unit_id}")
            seen.add(unit_id)

        known = set(ids)
        dependency_map: dict[str, list[str]] = {}
        for unit in self.work_units:
            dependency_map[unit.unit_id] = unit.dependencies
            if unit.unit_id in unit.dependencies:
                raise WorkUnitsValidationError(f"Work unit '{unit.unit_id}' depends on itself")
            for dependency in unit.dependencies:
                if dependency not in known:
                    raise WorkUnitsValidationError(
                        f"Unknown dependency '{dependency}' in work unit '{unit.unit_id}'"
                    )

        _validate_acyclic(dependency_map)
        return self


def _validate_acyclic(dependency_map: dict[str, list[str]]) -> None:
    visiting: set[str] = set()
    visited: set[str] = set()

    def dfs(node: str) -> None:
        if node in visited:
            return
        if node in visiting:
            raise WorkUnitsValidationError(f"Dependency cycle detected at '{node}'")

        visiting.add(node)
        for dependency in dependency_map.get(node, []):
            dfs(dependency)
        visiting.remove(node)
        visited.add(node)

    for node in dependency_map:
        dfs(node)


def parse_work_units_from_artifact(artifact: Mapping[str, object]) -> WorkUnitsPlan | None:
    """Parse and validate work_units[] from a planning artifact payload.

    Returns None when the artifact does not declare work_units.
    """
    raw = artifact.get("work_units")
    if raw is None:
        return None

    try:
        payload: dict[str, object] = {"work_units": raw}
        return WorkUnitsPlan.model_validate(payload)
    except Exception as exc:  # pragma: no cover - converted to domain error below
        if isinstance(exc, WorkUnitsValidationError):
            raise
        raise WorkUnitsValidationError(str(exc)) from exc

"""Planning work_units parsing and validation.

This module provides a typed parser for work_units[] declared in planning
artifacts. It intentionally focuses on schema and graph validation; execution
fanout remains orchestrator-owned.
"""

from __future__ import annotations

import re
from collections.abc import Mapping  # noqa: TC003
from pathlib import PurePosixPath

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator


class WorkUnitsValidationError(ValueError):
    """Raised when a planning artifact contains invalid work_units."""


_UNIT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_UNIT_ID_MAX_LEN = 64
# Token budget guard for planning artifact descriptions.
MAX_DESCRIPTION_CHARS = 4096


class WorkUnit(BaseModel):  # type: ignore[explicit-any]
    """Single planning work unit declaration."""

    model_config = ConfigDict(frozen=True)

    unit_id: str = Field(..., min_length=1)
    description: str = Field(..., min_length=1, max_length=MAX_DESCRIPTION_CHARS)
    allowed_directories: list[str] = Field(default_factory=list)
    dependencies: list[str] = Field(default_factory=list)

    @field_validator("unit_id")
    @classmethod
    def _validate_unit_id(cls, v: str) -> str:
        if _UNIT_ID_RE.fullmatch(v):
            return v
        if not v:
            raise ValueError("unit_id must be 1-64 chars from [a-zA-Z0-9_-] (got empty string)")
        if len(v) > _UNIT_ID_MAX_LEN:
            raise ValueError(
                f"unit_id must be at most {_UNIT_ID_MAX_LEN} chars (got length {len(v)}: {v!r})"
            )
        invalid_char = next((ch for ch in v if not re.fullmatch(r"[a-zA-Z0-9_-]", ch)), None)
        if invalid_char is not None:
            raise ValueError(
                f"unit_id contains invalid character {invalid_char!r}; allowed: [a-zA-Z0-9_-]"
            )
        raise ValueError(f"unit_id must match ^[a-zA-Z0-9_-]{{1,64}}$ (got: {v!r})")

    @field_validator("allowed_directories")
    @classmethod
    def _validate_allowed_directories(cls, v: list[str]) -> list[str]:
        return [_validate_relative_subpath(path) for path in v]


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


def _validate_relative_subpath(path: str) -> str:
    if not path:
        raise ValueError("allowed_directories entries must be non-empty")
    if "\\" in path:
        raise ValueError("allowed_directories entries must use '/' separators")

    parsed = PurePosixPath(path)
    if parsed.is_absolute():
        raise ValueError("allowed_directories entries must be relative paths")
    if ".." in parsed.parts:
        raise ValueError("allowed_directories entries must not contain '..'")
    return path


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

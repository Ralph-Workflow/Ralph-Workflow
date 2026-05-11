"""Planning work_units parsing and validation.

This module provides a typed parser for work_units[] declared in planning
artifacts. It intentionally focuses on schema and graph validation; execution
fanout remains orchestrator-owned.
"""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

from pydantic import BaseModel, ConfigDict, Field, field_validator, model_validator

if TYPE_CHECKING:
    from collections.abc import Mapping


class WorkUnitsValidationError(ValueError):
    """Raised when a planning artifact contains invalid work_units."""


_UNIT_ID_RE = re.compile(r"^[a-zA-Z0-9_-]{1,64}$")
_UNIT_ID_MAX_LEN = 64
# Token budget guard for planning artifact descriptions.
MAX_DESCRIPTION_CHARS = 4096

# Paths that workers must never declare as edit areas in same-workspace mode.
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


class _FrozenWorkUnitModel(BaseModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Private base for frozen work unit models.

    Owns ``model_config = ConfigDict(frozen=True)`` once so descendants do not
    repeat it. Pydantic v2 inherits ``model_config`` when descendants do not
    declare one of their own.
    """

    model_config = ConfigDict(frozen=True)


class WorkUnit(_FrozenWorkUnitModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Single planning work unit declaration."""

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


class WorkUnitsPlan(_FrozenWorkUnitModel):  # type: ignore[explicit-any]  # reason: external library has no type support, see docs/agents/type-ignore-policy.md#external-library
    """Typed representation of work_units[] in planning artifacts."""

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


def validate_for_same_workspace(plan: WorkUnitsPlan) -> None:
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
        if not unit.allowed_directories:
            raise WorkUnitsValidationError(
                f"Work unit '{unit.unit_id}' does not declare any allowed_directories. "
                "Each unit must declare the subdirectories it is permitted to edit. "
                "Choose disjoint subdirectories or merge the work units."
            )
        for d in unit.allowed_directories:
            _check_reserved(unit.unit_id, d)

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
    # Build a flat list of (unit_id, path_parts) sorted for deterministic errors
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

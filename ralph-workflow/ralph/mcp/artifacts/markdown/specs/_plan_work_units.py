from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, TypedDict

from ralph.checked_accessors import as_mapping
from ralph.mcp.artifacts.markdown.specs._plan_steps import PLAN_STEP_ID_PATTERN

if TYPE_CHECKING:
    from collections.abc import Mapping, MutableMapping, Sequence

    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection


class StepTarget(TypedDict):
    path: str
    action: str


class PlanStep(TypedDict, total=False):
    id: str
    number: int
    depends_on: list[int]
    targets: list[StepTarget]


class WorkUnit(TypedDict):
    unit_id: str
    description: str
    allowed_directories: list[str]
    allowed_paths: list[str]
    dependencies: list[str]
    step_ids: list[str]


_PLAN_WORDS = (
    "mini plan",
    "sub plan",
    "subplan",
    "work unit",
    "unit",
    "plan",
)


def attach_owned_step_ids(
    document: ParsedDocument,
    entries: Sequence[MutableMapping[str, object]],
    steps: Sequence[Mapping[str, object]],
    *,
    section_names: tuple[str, ...] = ("Work Units",),
) -> None:
    typed_entries = [_work_unit(entry) for entry in entries]
    typed_steps = [_plan_step(step) for step in steps]
    _attach_owned_step_ids(document, typed_entries, typed_steps, section_names=section_names)
    for entry, typed in zip(entries, typed_entries, strict=True):
        entry["step_ids"] = typed["step_ids"]
        entry["dependencies"] = typed["dependencies"]


def _work_unit(entry: Mapping[str, object]) -> WorkUnit:
    unit_id = entry["unit_id"]
    description = entry["description"]
    if not isinstance(unit_id, str) or not isinstance(description, str):
        msg = "work unit requires string unit_id and description"
        raise TypeError(msg)
    return {
        "unit_id": unit_id,
        "description": description,
        "allowed_directories": _optional_str_list(entry.get("allowed_directories")),
        "allowed_paths": _optional_str_list(entry.get("allowed_paths")),
        "dependencies": _optional_str_list(entry.get("dependencies")),
        "step_ids": _optional_str_list(entry.get("step_ids")),
    }


def _plan_step(step: Mapping[str, object]) -> PlanStep:
    result: PlanStep = {}
    step_id = step.get("id")
    if isinstance(step_id, str):
        result["id"] = step_id
    number = step.get("number")
    if isinstance(number, int) and not isinstance(number, bool):
        result["number"] = number
    raw_depends = step.get("depends_on")
    if isinstance(raw_depends, list):
        result["depends_on"] = [
            item for item in raw_depends if isinstance(item, int) and not isinstance(item, bool)
        ]
    raw_targets = step.get("targets")
    if isinstance(raw_targets, list):
        result["targets"] = _step_targets(raw_targets)
    return result


def _optional_str_list(value: object) -> list[str]:
    if not isinstance(value, list):
        empty: list[str] = []
        return empty
    return [item for item in value if isinstance(item, str)]


def _step_targets(raw_targets: list[object]) -> list[StepTarget]:
    targets: list[StepTarget] = []
    for item in raw_targets:
        try:
            mapping = as_mapping(item, field="targets")
        except TypeError:
            continue
        path = mapping.get("path")
        if not isinstance(path, str):
            continue
        action_value = mapping.get("action")
        action = action_value if isinstance(action_value, str) else "modify"
        targets.append({"path": path, "action": action})
    return targets


def _attach_owned_step_ids(
    document: ParsedDocument,
    entries: list[WorkUnit],
    steps: list[PlanStep],
    *,
    section_names: tuple[str, ...],
) -> None:
    owner_by_step: dict[str, str] = {}
    entry_by_id = {entry["unit_id"]: entry for entry in entries}

    for index, section in enumerate(document.sections):
        if section.name in section_names:
            _claim_work_unit_section_steps(
                _owned_sections(document, index),
                entry_by_id,
                owner_by_step,
            )

    key_to_ids: dict[str, list[str]] = {}
    for unit_id in entry_by_id:
        key_to_ids.setdefault(_owner_key(unit_id), []).append(unit_id)
    for index, section in enumerate(document.sections):
        if section.name in section_names:
            continue
        matching_ids = key_to_ids.get(_owner_key(section.name), [])
        if len(matching_ids) == 1:
            for owned_section in _owned_sections(document, index):
                _claim_section_steps(owned_section, matching_ids[0], owner_by_step)

    _claim_steps_by_target(entries, steps, owner_by_step)
    ordered_step_ids = [step_id for step in steps if (step_id := step.get("id")) is not None]
    for unit_id, entry in entry_by_id.items():
        owned = sorted(
            {step_id for step_id in ordered_step_ids if owner_by_step.get(step_id) == unit_id},
            key=ordered_step_ids.index,
        )
        if owned:
            entry["step_ids"] = owned
    _attach_cross_unit_dependencies(entries, steps)


def _owner_key(value: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()
    for marker in _PLAN_WORDS:
        if words == marker:
            return ""
        if words.startswith(f"{marker} "):
            words = words.removeprefix(f"{marker} ").strip()
        if words.endswith(f" {marker}"):
            words = words.removesuffix(f" {marker}").strip()
    return words


def _claim_section_steps(
    section: ParsedSection,
    unit_id: str,
    owner_by_step: dict[str, str],
) -> None:
    for block in section.blocks:
        if PLAN_STEP_ID_PATTERN.fullmatch(block.identifier) is not None:
            owner_by_step.setdefault(block.identifier, unit_id)


def _claim_work_unit_section_steps(
    sections: tuple[ParsedSection, ...],
    entry_by_id: dict[str, WorkUnit],
    owner_by_step: dict[str, str],
) -> None:
    unit_items = tuple(
        item for section in sections for item in section.items if item.identifier in entry_by_id
    )
    for block in (block for section in sections for block in section.blocks):
        if PLAN_STEP_ID_PATTERN.fullmatch(block.identifier) is None:
            continue
        owner_id: str | None = None
        owner_line = -1
        for item in unit_items:
            if owner_line < item.line < block.line:
                owner_id = item.identifier
                owner_line = item.line
        if owner_id is not None:
            owner_by_step.setdefault(block.identifier, owner_id)


def _owned_sections(
    document: ParsedDocument,
    owner_index: int,
) -> tuple[ParsedSection, ...]:
    owner = document.sections[owner_index]
    descendants: list[ParsedSection] = [owner]
    for section in document.sections[owner_index + 1 :]:
        if section.level <= owner.level:
            break
        descendants.append(section)
    return tuple(descendants)


def _claim_steps_by_target(
    entries: list[WorkUnit],
    steps: list[PlanStep],
    owner_by_step: dict[str, str],
) -> None:
    directories_by_unit = {
        entry["unit_id"]: list(entry["allowed_directories"]) for entry in entries
    }
    paths_by_unit = {
        entry["unit_id"]: {PurePosixPath(path) for path in entry["allowed_paths"]}
        for entry in entries
    }
    for step in steps:
        number = step.get("number")
        targets = step.get("targets")
        if number is None or targets is None or not targets:
            continue
        step_id = f"S-{number}"
        if step_id in owner_by_step:
            continue
        paths = [target["path"] for target in targets]
        candidates = [
            unit_id
            for unit_id, directories in directories_by_unit.items()
            if paths
            and all(
                _path_is_owned(path, directories) or PurePosixPath(path) in paths_by_unit[unit_id]
                for path in paths
            )
        ]
        if len(candidates) == 1:
            owner_by_step[step_id] = candidates[0]


def _attach_cross_unit_dependencies(
    entries: list[WorkUnit],
    steps: list[PlanStep],
) -> None:
    owner_by_step = {
        step_id: entry["unit_id"] for entry in entries for step_id in entry["step_ids"]
    }
    step_by_id = {
        f"S-{number}": step for step in steps if (number := step.get("number")) is not None
    }
    for entry in entries:
        unit_id = entry["unit_id"]
        dependencies = entry["dependencies"]
        for step_id in entry["step_ids"]:
            step = step_by_id.get(step_id)
            if step is None:
                continue
            depends_on = step.get("depends_on")
            if depends_on is None:
                continue
            for number in depends_on:
                owner = owner_by_step.get(f"S-{number}")
                if owner is not None and owner != unit_id and owner not in dependencies:
                    dependencies.append(owner)


def _path_is_owned(path: str, directories: list[str]) -> bool:
    path_parts = PurePosixPath(path).parts
    return any(
        path_parts[: len(directory_parts)] == directory_parts
        for directory in directories
        if (directory_parts := PurePosixPath(directory).parts)
    )


__all__ = ["attach_owned_step_ids"]

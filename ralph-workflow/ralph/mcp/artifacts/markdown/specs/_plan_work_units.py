"""Nested mini-plan ownership for explicit Markdown work units."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.markdown.specs._plan_steps import PLAN_STEP_ID_PATTERN

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection

type Content = dict[str, object]

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
    entries: list[Content],
    steps: list[Content],
) -> None:
    """Attach each nested step to at most one explicit work unit in place."""
    owner_by_step: dict[str, str] = {}
    entry_by_id = {cast("str", entry["unit_id"]): entry for entry in entries}

    for index, section in enumerate(document.sections):
        if section.name == "Work Units":
            _claim_work_unit_section_steps(
                _owned_sections(document, index),
                entry_by_id,
                owner_by_step,
            )

    key_to_ids: dict[str, list[str]] = {}
    for unit_id in entry_by_id:
        key_to_ids.setdefault(_owner_key(unit_id), []).append(unit_id)
    for index, section in enumerate(document.sections):
        if section.name == "Work Units":
            continue
        matching_ids = key_to_ids.get(_owner_key(section.name), [])
        if len(matching_ids) == 1:
            for owned_section in _owned_sections(document, index):
                _claim_section_steps(owned_section, matching_ids[0], owner_by_step)

    _claim_steps_by_target(entries, steps, owner_by_step)
    ordered_step_ids = [
        block.identifier
        for section in document.sections
        for block in section.blocks
        if PLAN_STEP_ID_PATTERN.fullmatch(block.identifier) is not None
    ]
    for unit_id, entry in entry_by_id.items():
        owned = [
            step_id
            for step_id in ordered_step_ids
            if owner_by_step.get(step_id) == unit_id
        ]
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
    entry_by_id: dict[str, Content],
    owner_by_step: dict[str, str],
) -> None:
    """Assign each nested step to its nearest preceding unit item."""
    unit_items = tuple(
        item
        for section in sections
        for item in section.items
        if item.identifier in entry_by_id
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
    """Return one heading plus descendants in its Markdown hierarchy."""
    owner = document.sections[owner_index]
    descendants: list[ParsedSection] = [owner]
    for section in document.sections[owner_index + 1 :]:
        if section.level <= owner.level:
            break
        descendants.append(section)
    return tuple(descendants)


def _claim_steps_by_target(
    entries: list[Content],
    steps: list[Content],
    owner_by_step: dict[str, str],
) -> None:
    directories_by_unit = {
        cast("str", entry["unit_id"]): [
            directory
            for directory in cast("list[object]", entry.get("allowed_directories", []))
            if isinstance(directory, str)
        ]
        for entry in entries
    }
    for step in steps:
        number = step.get("number")
        targets = step.get("targets")
        if not isinstance(number, int) or not isinstance(targets, list) or not targets:
            continue
        step_id = f"S-{number}"
        if step_id in owner_by_step:
            continue
        paths = [
            cast("str", target["path"])
            for target in targets
            if isinstance(target, dict) and isinstance(target.get("path"), str)
        ]
        candidates = [
            unit_id
            for unit_id, directories in directories_by_unit.items()
            if paths and all(_path_is_owned(path, directories) for path in paths)
        ]
        if len(candidates) == 1:
            owner_by_step[step_id] = candidates[0]


def _attach_cross_unit_dependencies(
    entries: list[Content],
    steps: list[Content],
) -> None:
    owner_by_step = {
        step_id: cast("str", entry["unit_id"])
        for entry in entries
        for step_id in cast("list[str]", entry.get("step_ids", []))
    }
    step_by_id = {
        f"S-{number}": step
        for step in steps
        if isinstance((number := step.get("number")), int)
    }
    for entry in entries:
        unit_id = cast("str", entry["unit_id"])
        dependencies = cast("list[str]", entry["dependencies"])
        for step_id in cast("list[str]", entry.get("step_ids", [])):
            step = step_by_id.get(step_id, {})
            raw_dependencies = step.get("depends_on", [])
            if not isinstance(raw_dependencies, list):
                continue
            for number in raw_dependencies:
                if not isinstance(number, int):
                    continue
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

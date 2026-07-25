"""Map execution-subagent Markdown subplans to safe dispatch records."""

from __future__ import annotations

import re
from pathlib import PurePosixPath
from typing import TYPE_CHECKING, cast

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection

type Content = dict[str, object]

_STEP_ID = re.compile(r"S-[1-9][0-9]*")
_SUBPLAN_PREFIX = re.compile(
    r"^\s*sub(?:[\s-]?plan)(?:\s*[:\-\u2013\u2014]\s*|\s+)"
    r"(?P<label>.+?)\s*$",
    re.IGNORECASE,
)
_SUBPLAN_SUFFIX = re.compile(
    r"^\s*(?P<label>.+?)(?:\s*[:\-\u2013\u2014]\s*|\s+)"
    r"sub(?:[\s-]?plan)\s*$",
    re.IGNORECASE,
)


def subplan_units_content(
    document: ParsedDocument,
    steps: list[Content],
) -> list[Content] | None:
    """Normalize targeted prefix/suffix Subplans into dependency-safe units.

    A Subplan without any file targets remains an ordinary main-session
    coordination section. This keeps fan-in integration and final verification
    out of same-workspace worker dispatch while retaining their global steps.
    """
    step_by_id = {
        f"S-{number}": step for step in steps if isinstance((number := step.get("number")), int)
    }
    candidates = [
        candidate
        for index, section in enumerate(document.sections)
        if (
            candidate := _candidate(
                section,
                _owned_sections(document, index),
                step_by_id,
            )
        )
        is not None
    ]
    if not candidates:
        return None

    owner_by_step = {
        step_id: candidate
        for candidate in candidates
        for step_id in cast(
            "list[str]", candidate["step_ids"]
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    }
    dispatchable_ids = {
        cast(
            "str", candidate["unit_id"]
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        for candidate in candidates
        if cast(
            "list[str]", candidate["allowed_directories"]
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    }
    dispatchable_ids = _remove_units_with_external_dependencies(
        candidates,
        owner_by_step,
        step_by_id,
        dispatchable_ids,
    )

    units: list[Content] = []
    for candidate in candidates:
        unit_id = cast(
            "str", candidate["unit_id"]
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        if unit_id not in dispatchable_ids:
            continue
        dependencies = _unit_dependencies(
            candidate,
            owner_by_step,
            step_by_id,
            dispatchable_ids,
        )
        unit = dict(candidate)
        if dependencies:
            unit["dependencies"] = dependencies
        units.append(unit)
    return units or None


def _candidate(
    section: ParsedSection,
    owned_sections: tuple[ParsedSection, ...],
    step_by_id: dict[str, Content],
) -> Content | None:
    label = _subplan_label(section.name)
    if label is None:
        return None
    step_ids = [
        block.identifier
        for owned_section in owned_sections
        for block in owned_section.blocks
        if _STEP_ID.fullmatch(block.identifier) is not None
    ]
    if not step_ids:
        return None
    return {
        "unit_id": f"subplan-{step_ids[0].casefold()}",
        "description": label,
        "allowed_directories": _target_directories(step_ids, step_by_id),
        "step_ids": step_ids,
    }


def _owned_sections(
    document: ParsedDocument,
    owner_index: int,
) -> tuple[ParsedSection, ...]:
    """Return an owner heading and all of its nested descendant sections."""
    owner = document.sections[owner_index]
    descendants: list[ParsedSection] = [owner]
    for section in document.sections[owner_index + 1 :]:
        if section.level <= owner.level:
            break
        descendants.append(section)
    return tuple(descendants)


def _subplan_label(name: str) -> str | None:
    for pattern in (_SUBPLAN_PREFIX, _SUBPLAN_SUFFIX):
        match = pattern.fullmatch(name)
        if match is not None:
            return cast(
                "str", match.group("label")
            ).strip()  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    return None


def _target_directories(
    step_ids: list[str],
    step_by_id: dict[str, Content],
) -> list[str]:
    directories: list[str] = []
    for step_id in step_ids:
        step = step_by_id.get(step_id, {})
        raw_targets = step.get("targets", [])
        if not isinstance(raw_targets, list):
            continue
        for raw_target in raw_targets:
            if not isinstance(raw_target, dict):
                continue
            path = raw_target.get("path")
            if not isinstance(path, str):
                continue
            parent = str(PurePosixPath(path).parent)
            directory = path if parent == "." else parent
            if directory not in directories:
                directories.append(directory)
    return directories


def _remove_units_with_external_dependencies(
    candidates: list[Content],
    owner_by_step: dict[str, Content],
    step_by_id: dict[str, Content],
    dispatchable_ids: set[str],
) -> set[str]:
    safe_ids = set(dispatchable_ids)
    changed = True
    while changed:
        changed = False
        for candidate in candidates:
            unit_id = cast(
                "str", candidate["unit_id"]
            )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
            if unit_id not in safe_ids:
                continue
            for dependency in _cross_unit_dependency_steps(candidate, step_by_id):
                owner = owner_by_step.get(dependency)
                owner_id = (
                    cast("str", owner["unit_id"]) if owner is not None else None
                )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
                if owner_id != unit_id and owner_id not in safe_ids:
                    safe_ids.remove(unit_id)
                    changed = True
                    break
    return safe_ids


def _unit_dependencies(
    candidate: Content,
    owner_by_step: dict[str, Content],
    step_by_id: dict[str, Content],
    dispatchable_ids: set[str],
) -> list[str]:
    unit_id = cast(
        "str", candidate["unit_id"]
    )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
    dependencies: list[str] = []
    for dependency in _cross_unit_dependency_steps(candidate, step_by_id):
        owner = owner_by_step.get(dependency)
        if owner is None:
            continue
        owner_id = cast(
            "str", owner["unit_id"]
        )  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        if owner_id != unit_id and owner_id in dispatchable_ids and owner_id not in dependencies:
            dependencies.append(owner_id)
    return dependencies


def _cross_unit_dependency_steps(
    candidate: Content,
    step_by_id: dict[str, Content],
) -> list[str]:
    dependencies: list[str] = []
    for step_id in cast(
        "list[str]", candidate["step_ids"]
    ):  # cast-policy: seam: structural boundary (sqlite Row / lazy module attr / protocol conferee)
        step = step_by_id.get(step_id, {})
        raw_dependencies = step.get("depends_on", [])
        if not isinstance(raw_dependencies, list):
            continue
        for number in raw_dependencies:
            if isinstance(number, int):
                dependency = f"S-{number}"
                if dependency not in dependencies:
                    dependencies.append(dependency)
    return dependencies


__all__ = ["subplan_units_content"]

"""Sanity-only plan acceptance with optional canonical execution extraction."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown._spec import Content, MdArtifactSpec, parse_and_validate
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.markdown.specs._plan_not_a_plan import detect_not_a_plan
from ralph.mcp.artifacts.markdown.specs._plan_work_units import attach_owned_step_ids

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
    from ralph.mcp.artifacts.markdown._document import ParsedDocument

_STEP_ID = re.compile(r"^S-([1-9][0-9]*)$")
_TARGET = re.compile(r"^-\s+(?:(modify|create|delete|read|run|test|verify)\s+)?(`?)(\S+?)\2$")
# Python's int() rejects integer strings longer than 4300 digits by default;
# tolerate that boundary during best-effort extraction rather than raising
# the whole submission.
_MAX_INT_DIGITS = 4300


def _safe_int(value: str) -> int | None:
    """Parse a small positive integer; return None on size or value errors."""
    if not value or len(value) > _MAX_INT_DIGITS:
        return None
    try:
        return int(value)
    except ValueError:
        return None


def analyze_plan_document(text: str) -> tuple[Content, list[Diagnostic], list[object]]:
    """Accept readable plan prose and return optional structure, without shape findings."""
    content, diagnostics = parse_and_validate(text, PLAN_SPEC)
    return content, diagnostics, []


def _steps(document: ParsedDocument) -> list[Content]:
    steps: list[Content] = []
    seen: set[str] = set()
    for section in document.sections:
        for block in section.blocks:
            match = _STEP_ID.fullmatch(block.identifier)
            if match is None or block.identifier in seen:
                continue
            seen.add(block.identifier)
            number = _safe_int(match[1])
            if number is None:
                # Oversized numeric token: keep the prose block but skip the
                # numeric step entry so we never raise on extraction.
                continue
            lines = [line.text for line in block.lines]
            step: Content = {
                "id": block.identifier,
                "number": number,
                "title": block.title,
                "content": "\n".join(lines).strip(),
                "depends_on": [],
                "targets": [],
            }
            targets: list[Content] = []
            dependencies: list[int] = []
            in_files = False
            for line in lines:
                label, separator, value = line.strip().partition(":")
                if separator:
                    in_files = label.casefold() == "files"
                    if label.casefold() == "depends on":
                        dependencies.extend(
                            ref_number
                            for token in _values(value)
                            if (ref := _STEP_ID.fullmatch(token)) is not None
                            and (ref_number := _safe_int(ref[1])) is not None
                        )
                    elif label.casefold() in {"type", "verify", "expect", "location"}:
                        key = {
                            "type": "step_type",
                            "verify": "verify_command",
                            "expect": "expected_outcome",
                            "location": "location",
                        }[label.casefold()]
                        step[key] = value.strip()
                elif in_files and (target := _TARGET.fullmatch(line.strip())) is not None:
                    targets.append({"path": target[3], "action": target[1] or "modify"})
            step["depends_on"] = sorted(set(dependencies), key=dependencies.index)
            step["targets"] = targets
            steps.append(step)
    return steps


def _values(value: str) -> list[str]:
    return [part.strip().strip("`") for part in value.replace(",", " ").split() if part.strip()]


def _units(document: ParsedDocument, name: str) -> list[Content]:
    entries: list[Content] = []
    seen: set[str] = set()
    for section in document.sections:
        if section.name != name:
            continue
        for item in section.items:
            identifier = item.identifier
            if not identifier or identifier in seen:
                continue
            seen.add(identifier)
            entry: Content = {
                "unit_id": identifier,
                "description": item.text,
                "allowed_directories": [],
                "allowed_paths": [],
                "dependencies": [],
                "step_ids": [],
            }
            for field in item.fields:
                label, separator, value = field.text.strip().partition(":")
                key = {
                    "directories": "allowed_directories",
                    "paths": "allowed_paths",
                    "depends on": "dependencies",
                }.get(label.casefold())
                if separator and key is not None:
                    entry[key] = _values(value)
            entries.append(entry)
    return entries


def _is_step_metadata_line(text: str) -> bool:
    """Return whether a step-body line is safely represented in extraction."""
    stripped = text.strip()
    return _TARGET.match(stripped) is not None or stripped.casefold().startswith(
        ("files:", "depends on:", "satisfies:", "verify:", "expect:")
    )


def _has_residual_work(document: ParsedDocument, unit_step_ids: set[str]) -> bool:
    """Keep prose that extraction cannot safely assign out of fan-out."""
    seen_step_ids: set[str] = set()
    for section in document.sections:
        is_unit_section = section.name in {"Work Units", "Parallel Plan"}
        # A non-unit heading can itself name work. Extraction has no safe
        # ownership for its text, including a heading that contains a unit
        # section, so retain it for the main agent rather than omit it.
        unrepresented_heading = not is_unit_section and not (
            section.lines or section.items or section.blocks
        )
        for block in section.blocks:
            if _STEP_ID.fullmatch(block.identifier) and block.identifier in seen_step_ids:
                return True
            seen_step_ids.add(block.identifier)
        if section.lines or unrepresented_heading:
            return True
        if section.blocks:
            if any(block.identifier not in unit_step_ids for block in section.blocks):
                return True
            if any(
                not _is_step_metadata_line(line.text) and (not is_unit_section or "/" in line.text)
                for block in section.blocks
                for line in block.lines
            ):
                return True
            continue
        if section.items and not is_unit_section:
            return True
    return False


def _to_content(document: ParsedDocument) -> Content:
    content: Content = {}
    steps = _steps(document)
    if steps:
        content["steps"] = steps
    units = _units(document, "Work Units")
    parallel = _units(document, "Parallel Plan")
    seen_unit_ids: set[str] = set()
    ambiguous_unit_ids: set[str] = set()
    for section in document.sections:
        if section.name in {"Work Units", "Parallel Plan"}:
            for item in section.items:
                if item.identifier in seen_unit_ids:
                    ambiguous_unit_ids.add(item.identifier)
                seen_unit_ids.add(item.identifier)
    if ambiguous_unit_ids:
        content["unextractable_work_units"] = True
    else:
        attach_owned_step_ids(
            document, [*units, *parallel], steps, section_names=("Work Units", "Parallel Plan")
        )
    if any(
        line.text.startswith("-")
        for section in document.sections
        if section.name in {"Work Units", "Parallel Plan"}
        for line in section.lines
    ):
        content["unextractable_work_units"] = True
    unit_step_ids: set[str] = set()
    for unit in (*units, *parallel):
        raw_step_ids = unit.get("step_ids")
        if isinstance(raw_step_ids, list):
            unit_step_ids.update(step_id for step_id in raw_step_ids if isinstance(step_id, str))
    if (units or parallel) and _has_residual_work(document, unit_step_ids):
        # Keep non-unit prose or unowned blocks in the main session instead
        # of allowing fan-out to silently drop it.
        content["unextractable_work_units"] = True
    if units:
        content["work_units"] = units
    if parallel:
        content["parallel_plan"] = [
            {
                "id": unit["unit_id"],
                "description": unit["description"],
                "edit_area": {
                    "directories": unit["allowed_directories"],
                    "paths": unit["allowed_paths"],
                },
                "depends_on": unit["dependencies"],
                "step_ids": unit["step_ids"],
            }
            for unit in parallel
        ]
    if document.frontmatter.get("noop", "").casefold() == "true":
        content["noop"] = True
    return content


def _normalize(content: Content) -> Content:
    return content


PLAN_SPEC = MdArtifactSpec(
    artifact_type="plan",
    required_frontmatter=frozenset(),
    sections={},
    to_content=_to_content,
    normalize_content=_normalize,
    validate_text=detect_not_a_plan,
    allow_nested_headings=True,
)
register_spec(PLAN_SPEC)


__all__ = ["PLAN_SPEC", "analyze_plan_document"]

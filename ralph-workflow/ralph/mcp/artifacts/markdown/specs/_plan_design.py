"""Design-section field mapping for the plan markdown grammar.

Maps recognized ``## Design`` labels into the optional canonical design
model. Design is descriptive: incomplete groups and unfamiliar typed values
stay in the source Markdown but do not block a plan whose executor-consumed
anchors are valid.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown._fields import FieldKind, ParsedFields, parse_fields

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine
    from ralph.mcp.artifacts.markdown._spec import Content

_BOOLEANS = {"yes": True, "true": True, "no": False, "false": False}

_DESIGN_FIELDS: dict[str, FieldKind] = {
    "profile": "scalar",
    "outcome": "scalar",
    "constraints": "scalar",
    "invariants": "bullet_list",
    "architecture": "scalar",
    "non-goals": "bullet_list",
    "black box": "scalar",
    "forbidden in tests": "inline_list",
    "test layers": "inline_list",
    "clock injection": "scalar",
    "max unit test seconds": "scalar",
    "di required": "scalar",
    "di preferred": "inline_list",
    "di forbidden": "inline_list",
    "di notes": "scalar",
    "guard commands": "bullet_list",
    "expected outputs": "bullet_list",
    "drift sources": "inline_list",
    "on drift": "scalar",
    "refactor approach": "scalar",
    "dead code": "scalar",
    "preserve api": "scalar",
    "temporary hacks": "scalar",
}


def design_content(
    document: ParsedDocument,
    criteria: Content | None,
    diagnostics: list[Diagnostic],
) -> Content | None:
    """Map the optional Design section (plus acceptance criteria) to content."""
    sections = document.sections_named("Design")
    if not sections:
        return {"acceptance_criteria": criteria} if criteria is not None else None
    fields = parse_fields(
        [line for section in sections for line in section.lines],
        _DESIGN_FIELDS,
        section="Design",
        context="Design",
        prose_allowed=True,
        diagnostics=diagnostics,
    )
    design: Content = {}
    notes = "\n".join(line.text for line in fields.prose)
    if notes:
        design["notes"] = notes
    for key, name in (("profile", "planning_profile"), ("outcome", "outcome")):
        scalar = fields.scalars.get(key)
        if scalar is not None:
            design[name] = scalar.text
    _constraints_group(fields, design)
    non_goals = fields.lists.get("non-goals")
    if non_goals is not None:
        design["non_goals"] = {"items": [entry.text for entry in non_goals]}
    _testability_group(fields, design)
    _di_group(fields, design)
    _drift_group(fields, design)
    _refactor_group(fields, design)
    if criteria is not None:
        design["acceptance_criteria"] = criteria
    return design or None


def _parsed_bool(scalar: ParsedLine) -> bool | None:
    return _BOOLEANS.get(scalar.text.casefold())


def _parsed_float(scalar: ParsedLine) -> float | None:
    try:
        return float(scalar.text)
    except ValueError:
        return None


def _constraints_group(
    fields: ParsedFields,
    design: Content,
) -> None:
    text = fields.scalars.get("constraints")
    invariants = fields.lists.get("invariants")
    architecture = fields.scalars.get("architecture")
    if text is None:
        return
    group: Content = {"text": text.text}
    if invariants is not None:
        group["invariants"] = [entry.text for entry in invariants]
    if architecture is not None:
        group["architecture_style"] = architecture.text
    design["constraints"] = group


def _testability_group(
    fields: ParsedFields,
    design: Content,
) -> None:
    black_box = fields.scalars.get("black box")
    forbidden = fields.lists.get("forbidden in tests")
    layers = fields.lists.get("test layers")
    clock = fields.scalars.get("clock injection")
    seconds = fields.scalars.get("max unit test seconds")
    if black_box is None:
        return
    value = _parsed_bool(black_box)
    if value is None:
        return
    group: Content = {"must_be_black_box": value}
    if forbidden is not None:
        group["forbidden_in_tests"] = [entry.text for entry in forbidden]
    if layers is not None:
        group["required_test_layers"] = [entry.text for entry in layers]
    if clock is not None:
        clock_value = _parsed_bool(clock)
        if clock_value is not None:
            group["clock_injection_required"] = clock_value
    if seconds is not None:
        seconds_value = _parsed_float(seconds)
        if seconds_value is not None:
            group["max_unit_test_seconds"] = seconds_value
    design["testability"] = group


def _di_group(fields: ParsedFields, design: Content) -> None:
    required = fields.scalars.get("di required")
    preferred = fields.lists.get("di preferred")
    forbidden = fields.lists.get("di forbidden")
    notes = fields.scalars.get("di notes")
    if required is None:
        return
    value = _parsed_bool(required)
    if value is None:
        return
    group: Content = {"required_for_testability": value}
    if preferred is not None:
        group["preferred_patterns"] = [entry.text for entry in preferred]
    if forbidden is not None:
        group["forbidden_patterns"] = [entry.text for entry in forbidden]
    if notes is not None:
        group["notes"] = notes.text
    design["dependency_injection"] = group


def _drift_group(fields: ParsedFields, design: Content) -> None:
    group: Content = {}
    for key, name in (("guard commands", "guard_commands"), ("expected outputs", "expected_outputs")):
        entries = fields.lists.get(key)
        if entries is not None:
            group[name] = [entry.text for entry in entries]
    sources = fields.lists.get("drift sources")
    if sources is not None:
        group["sources"] = [entry.text for entry in sources]
    on_drift = fields.scalars.get("on drift")
    if on_drift is not None:
        group["on_drift_action"] = on_drift.text
    if group:
        design["drift_detection"] = group


def _refactor_group(
    fields: ParsedFields,
    design: Content,
) -> None:
    approach = fields.scalars.get("refactor approach")
    dead_code = fields.scalars.get("dead code")
    preserve = fields.scalars.get("preserve api")
    hacks = fields.scalars.get("temporary hacks")
    if approach is None:
        return
    group: Content = {"approach": approach.text}
    if dead_code is not None:
        group["dead_code_policy"] = dead_code.text
    if preserve is not None:
        value = _parsed_bool(preserve)
        if value is not None:
            group["preserve_public_api"] = value
    if hacks is not None:
        value = _parsed_bool(hacks)
        if value is not None:
            group["allow_temporary_hacks"] = value
    design["refactor_strategy"] = group


__all__ = ["design_content"]

from __future__ import annotations

import re
from contextlib import suppress
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._fields import FieldKind, ParsedFields, parse_fields
from ralph.mcp.artifacts.markdown._parser import parse_markdown_document
from ralph.mcp.artifacts.markdown._spec import Content, MdArtifactSpec
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.markdown.specs._plan_design import design_content
from ralph.mcp.artifacts.markdown.specs._plan_evidence import evidence_content
from ralph.mcp.artifacts.markdown.specs._plan_steps import resolve_step_references, step_number_map
from ralph.mcp.artifacts.markdown.specs._plan_subplans import subplan_units_content
from ralph.mcp.artifacts.markdown.specs._plan_work_units import attach_owned_step_ids
from ralph.mcp.artifacts.plan._section_registry import PLAN_ARTIFACT_TYPE

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem
    from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine
_ACCEPTANCE_CRITERION_ID_PATTERN = re.compile("^AC-[0-9]{2,}$")
_VERIFICATION_ITEM_ID_PATTERN = re.compile("^V-[0-9]+$")
_BINARY_CONTROL_CHARACTERS = frozenset(chr(number) for number in range(32)) - {"\t", "\n", "\r"}
_SUMMARY_FIELDS: dict[str, FieldKind] = {"intent": "scalar", "coverage": "inline_list"}
_SCOPE_ITEM_FIELDS: dict[str, FieldKind] = {"category": "scalar", "count": "scalar"}
_SKILLS_FIELDS: dict[str, FieldKind] = {"skills": "inline_list", "mcps": "inline_list"}
_STEP_FIELDS: dict[str, FieldKind] = {
    "type": "scalar",
    "priority": "scalar",
    "files": "bullet_list",
    "depends on": "inline_list",
    "satisfies": "inline_list",
    "verify": "scalar",
    "expect": "scalar",
    "location": "scalar",
    "rationale": "scalar",
    "evidence": "bullet_list",
}
_CRITICAL_FILE_FIELDS: dict[str, FieldKind] = {
    "action": "scalar",
    "changes": "scalar",
    "purpose": "scalar",
}
_CONSTRAINTS_FIELDS: dict[str, FieldKind] = {
    "must not break": "bullet_list",
    "must keep working": "bullet_list",
    "performance budget": "scalar",
    "security posture": "scalar",
}
_CRITERION_FIELDS: dict[str, FieldKind] = {
    "satisfied by": "inline_list",
    "verify": "scalar",
    "expect": "scalar",
    "evidence": "scalar",
}
_RISK_FIELDS: dict[str, FieldKind] = {"severity": "scalar", "mitigation": "scalar"}
_VERIFICATION_FIELDS: dict[str, FieldKind] = {
    "expect": "scalar",
    "timeout": "scalar",
    "cwd": "scalar",
}
_PARALLEL_FIELDS: dict[str, FieldKind] = {
    "depends on": "inline_list",
    "paths": "inline_list",
    "directories": "inline_list",
}
_WORK_UNIT_FIELDS: dict[str, FieldKind] = {
    "depends on": "inline_list",
    "directories": "inline_list",
}


def _merged_lines(document: ParsedDocument, name: str) -> list[ParsedLine]:
    return [line for section in document.sections_named(name) for line in section.lines]


def _merged_items(document: ParsedDocument, name: str) -> list[ParsedItem]:
    return [item for section in document.sections_named(name) for item in section.items]


def _is_acceptance_criterion(item: ParsedItem) -> bool:
    return _ACCEPTANCE_CRITERION_ID_PATTERN.fullmatch(item.identifier) is not None


def _verification_items(document: ParsedDocument) -> list[ParsedItem]:
    return [
        item
        for section in document.sections
        for item in section.items
        if section.name == "Verification"
        or _VERIFICATION_ITEM_ID_PATTERN.fullmatch(item.identifier) is not None
    ]


def _fan_out_unit_items(
    document: ParsedDocument, name: str, diagnostics: list[Diagnostic]
) -> list[ParsedItem]:
    unit_items: list[ParsedItem] = []
    for section in document.sections_named(name):
        section_units = [item for item in section.items if not _is_acceptance_criterion(item)]
        unit_items.extend(section_units)
    return unit_items


def _item_fields(
    item: ParsedItem,
    table: Mapping[str, FieldKind],
    section: str,
    diagnostics: list[Diagnostic],
    *,
    prose_allowed: bool = True,
) -> ParsedFields:
    fields = parse_fields(
        item.fields,
        table,
        section=section,
        context=f"item {item.identifier!r}",
        prose_allowed=prose_allowed,
        diagnostics=diagnostics,
    )
    return fields


def _with_prose(text: str, fields: ParsedFields) -> str:
    prose = "\n".join(line.text for line in fields.prose)
    return f"{text}\n{prose}" if prose else text


def _verification_expectations(document: ParsedDocument) -> dict[str, str]:
    expectations: dict[str, str] = {}
    for item in _verification_items(document):
        fields = parse_fields(
            item.fields,
            _VERIFICATION_FIELDS,
            section="Verification",
            context=f"item {item.identifier!r}",
            prose_allowed=True,
            diagnostics=[],
        )
        expect = fields.scalars.get("expect")
        if expect is not None:
            expectations[item.text] = expect.text
    return expectations


def _summary_content(document: ParsedDocument, diagnostics: list[Diagnostic]) -> Content:
    fields = parse_fields(
        _merged_lines(document, "Summary"),
        _SUMMARY_FIELDS,
        section="Summary",
        context="Summary",
        prose_allowed=True,
        diagnostics=diagnostics,
    )
    summary: Content = {}
    prose = [line.text for line in fields.prose]
    prose.extend(item.text for item in _merged_items(document, "Summary"))
    context = "\n".join(prose)
    if context:
        summary["context"] = context
    intent = fields.scalars.get("intent")
    if intent is not None:
        summary["intent"] = intent.text
    coverage = [entry.text for entry in fields.lists.get("coverage", [])]
    if coverage:
        summary["coverage_areas"] = coverage
    scope_items = _scope_items(document, diagnostics)
    if scope_items:
        summary["scope_items"] = scope_items
    intent_verb = document.frontmatter.get("intent_verb")
    if intent_verb is not None:
        summary["intent_verb"] = intent_verb
    return summary


def _scope_items(document: ParsedDocument, diagnostics: list[Diagnostic]) -> list[Content]:
    items: list[Content] = []
    for item in _merged_items(document, "Scope"):
        fields = _item_fields(item, _SCOPE_ITEM_FIELDS, "Scope", diagnostics)
        scope_item: Content = {"text": _with_prose(item.text, fields)}
        category = fields.scalars.get("category")
        if category is not None:
            scope_item["category"] = category.text
        count = fields.scalars.get("count")
        if count is not None:
            scope_item["count"] = count.text
        items.append(scope_item)
    return items


def _skills_content(document: ParsedDocument, diagnostics: list[Diagnostic]) -> Content | None:
    sections = document.sections_named("Skills MCP")
    if not sections:
        return None
    fields = parse_fields(
        _merged_lines(document, "Skills MCP"),
        _SKILLS_FIELDS,
        section="Skills MCP",
        context="Skills MCP",
        prose_allowed=True,
        diagnostics=diagnostics,
    )
    skills = fields.lists.get("skills")
    mcps = fields.lists.get("mcps")
    if skills is None and mcps is None:
        return None
    return {
        "skills": [entry.text for entry in skills or []],
        "mcps": [entry.text for entry in mcps or []],
    }


def _target_content(entry: ParsedLine, context: str, diagnostics: list[Diagnostic]) -> Content:
    head, _, rest = entry.text.partition(" ")
    rest = rest.strip()
    if rest:
        return {"path": rest, "action": head}
    return {"path": entry.text, "action": "modify"}


def _steps_content(
    document: ParsedDocument,
    numbers: Mapping[str, int],
    verification_expectations: Mapping[str, str],
    diagnostics: list[Diagnostic],
) -> list[Content]:
    steps: list[Content] = []
    seen: set[str] = set()
    blocks = [block for section in document.sections for block in section.blocks]
    for block in blocks:
        number = numbers.get(block.identifier)
        if number is None or block.identifier in seen:
            continue
        seen.add(block.identifier)
        context = f"step {block.identifier!r}"
        fields = parse_fields(
            block.lines,
            _STEP_FIELDS,
            section="Steps",
            context=context,
            prose_allowed=True,
            diagnostics=diagnostics,
        )
        step: Content = {"number": number, "title": block.title}
        prose = "\n".join(line.text for line in fields.prose)
        if prose:
            step["content"] = prose
        step_type_field = fields.scalars.get("type")
        step_type = step_type_field.text if step_type_field is not None else None
        if step_type is not None:
            step["step_type"] = step_type
        priority = fields.scalars.get("priority")
        if priority is not None:
            step["priority"] = priority.text
        files = fields.lists.get("files")
        if files is not None:
            step["targets"] = [_target_content(entry, context, diagnostics) for entry in files]
        depends_on = fields.lists.get("depends on")
        if depends_on is not None:
            step["depends_on"] = resolve_step_references(
                depends_on, numbers, section="Steps", context=context, diagnostics=diagnostics
            )
        satisfies = fields.lists.get("satisfies")
        if satisfies is not None:
            step["satisfies"] = [entry.text for entry in satisfies]
        for key, name in (
            ("verify", "verify_command"),
            ("expect", "expected_outcome"),
            ("location", "location"),
            ("rationale", "rationale"),
        ):
            scalar = fields.scalars.get(key)
            if scalar is not None:
                step[name] = scalar.text
        evidence = fields.lists.get("evidence")
        if evidence is not None:
            step["expected_evidence"] = [
                evidence_content(entry, context, diagnostics) for entry in evidence
            ]
        verify = step.get("verify_command")
        if (
            isinstance(verify, str)
            and "expected_outcome" not in step
            and (verify in verification_expectations)
        ):
            step["expected_outcome"] = verification_expectations[verify]
        steps.append(step)
    return steps


def _critical_files_content(
    document: ParsedDocument, diagnostics: list[Diagnostic]
) -> Content | None:
    sections = document.sections_named("Critical Files")
    if not sections:
        return None
    primary: list[Content] = []
    reference: list[Content] = []
    for item in _merged_items(document, "Critical Files"):
        fields = _item_fields(item, _CRITICAL_FILE_FIELDS, "Critical Files", diagnostics)
        purpose = fields.scalars.get("purpose")
        action = fields.scalars.get("action")
        changes = fields.scalars.get("changes")
        if purpose is not None and action is None and (changes is None):
            reference.append({"path": item.text, "purpose": purpose.text})
            continue
        entry: Content = {"path": item.text}
        entry["action"] = action.text if action is not None else "modify"
        if changes is not None:
            entry["estimated_changes"] = changes.text
        primary.append(entry)
    critical: Content = {"primary_files": primary}
    if reference:
        critical["reference_files"] = reference
    return critical


def _constraints_content(document: ParsedDocument, diagnostics: list[Diagnostic]) -> Content | None:
    if not document.sections_named("Constraints"):
        return None
    fields = parse_fields(
        _merged_lines(document, "Constraints"),
        _CONSTRAINTS_FIELDS,
        section="Constraints",
        context="Constraints",
        prose_allowed=True,
        diagnostics=diagnostics,
    )
    constraints: Content = {}
    for key, name in (
        ("must not break", "must_not_break"),
        ("must keep working", "must_keep_working"),
    ):
        entries = fields.lists.get(key)
        if entries is not None:
            constraints[name] = [entry.text for entry in entries]
    for key, name in (
        ("performance budget", "performance_budget"),
        ("security posture", "security_posture"),
    ):
        scalar = fields.scalars.get(key)
        if scalar is not None:
            constraints[name] = scalar.text
    return constraints or None


def _acceptance_criteria_content(
    document: ParsedDocument,
    verification_expectations: Mapping[str, str],
    diagnostics: list[Diagnostic],
) -> Content | None:
    items = [
        item
        for section in document.sections
        for item in section.items
        if section.name == "Acceptance Criteria" or _is_acceptance_criterion(item)
    ]
    if not items:
        return None
    numbers = step_number_map(document, [])
    criteria: list[Content] = []
    for item in items:
        fields = _item_fields(item, _CRITERION_FIELDS, "Acceptance Criteria", diagnostics)
        criterion: Content = {"id": item.identifier, "description": _with_prose(item.text, fields)}
        satisfied_by = fields.lists.get("satisfied by")
        if satisfied_by is not None:
            criterion["satisfied_by_steps"] = resolve_step_references(
                satisfied_by,
                numbers,
                section="Acceptance Criteria",
                context=f"criterion {item.identifier!r}",
                diagnostics=diagnostics,
            )
        verify = fields.scalars.get("verify")
        expect = fields.scalars.get("expect")
        expected_text = (
            expect.text
            if expect is not None
            else verification_expectations.get(verify.text)
            if verify is not None
            else None
        )
        if verify is not None:
            criterion["verification_step"] = verify.text
            if expected_text is not None:
                criterion["expected_outcome"] = expected_text
        evidence = fields.scalars.get("evidence")
        if evidence is not None:
            criterion["evidence_path"] = evidence.text
        criteria.append(criterion)
    return {"criteria": criteria}


def _risks_content(document: ParsedDocument, diagnostics: list[Diagnostic]) -> list[Content]:
    risks: list[Content] = []
    for item in _merged_items(document, "Risks"):
        fields = _item_fields(item, _RISK_FIELDS, "Risks", diagnostics)
        risk: Content = {"risk": _with_prose(item.text, fields)}
        mitigation = fields.scalars.get("mitigation")
        if mitigation is not None:
            risk["mitigation"] = mitigation.text
        severity = fields.scalars.get("severity")
        if severity is not None:
            risk["severity"] = severity.text
        risks.append(risk)
    return risks


def _verification_content(document: ParsedDocument, diagnostics: list[Diagnostic]) -> list[Content]:
    entries: list[Content] = []
    for item in _verification_items(document):
        fields = _item_fields(item, _VERIFICATION_FIELDS, "Verification", diagnostics)
        entry: Content = {"method": item.text}
        expect = fields.scalars.get("expect")
        if expect is not None:
            entry["expected_outcome"] = expect.text
        timeout = fields.scalars.get("timeout")
        if timeout is not None:
            with suppress(ValueError):
                entry["timeout_seconds"] = int(timeout.text)
        cwd = fields.scalars.get("cwd")
        if cwd is not None:
            entry["cwd"] = cwd.text
        entries.append(entry)
    return entries


def _parallel_plan_content(
    document: ParsedDocument, steps: list[Content], diagnostics: list[Diagnostic]
) -> list[Content] | None:
    sections = document.sections_named("Parallel Plan")
    if not sections:
        return None
    items = _fan_out_unit_items(document, "Parallel Plan", diagnostics)
    entries: list[Content] = []
    ownership: list[Content] = []
    for item in items:
        fields = _item_fields(item, _PARALLEL_FIELDS, "Parallel Plan", diagnostics)
        directories = [entry.text for entry in fields.lists.get("directories", [])]
        paths = [entry.text for entry in fields.lists.get("paths", [])]
        dependencies = [entry.text for entry in fields.lists.get("depends on", [])]
        ownership.append(
            {
                "unit_id": item.identifier,
                "allowed_directories": directories + paths,
                "dependencies": dependencies,
            }
        )
        entries.append(
            {
                "id": item.identifier,
                "description": item.text,
                "edit_area": {"paths": paths, "directories": directories},
                "depends_on": dependencies,
            }
        )
    attach_owned_step_ids(document, ownership, steps, section_name="Parallel Plan")
    for entry, unit in zip(entries, ownership, strict=True):
        entry["step_ids"] = unit.get("step_ids", [])
    return entries


def _work_units_content(
    document: ParsedDocument, steps: list[Content], diagnostics: list[Diagnostic]
) -> list[Content] | None:
    sections = document.sections_named("Work Units")
    if not sections:
        return None
    items = _fan_out_unit_items(document, "Work Units", diagnostics)
    entries: list[Content] = []
    for item in items:
        fields = _item_fields(item, _WORK_UNIT_FIELDS, "Work Units", diagnostics)
        entry: Content = {
            "unit_id": item.identifier,
            "description": item.text,
            "allowed_directories": [entry.text for entry in fields.lists.get("directories", [])],
            "dependencies": [entry.text for entry in fields.lists.get("depends on", [])],
        }
        entries.append(entry)
    attach_owned_step_ids(document, entries, steps)
    return entries


def _analyze(document: ParsedDocument) -> tuple[Content, list[Diagnostic]]:
    diagnostics: list[Diagnostic] = []
    numbers = step_number_map(document, diagnostics)
    verification_expectations = _verification_expectations(document)
    steps = _steps_content(document, numbers, verification_expectations, diagnostics)
    content: Content = {"steps": steps}
    summary = _summary_content(document, diagnostics)
    if summary:
        content["summary"] = summary
    skills = _skills_content(document, diagnostics)
    if skills is not None:
        content["skills_mcp"] = skills
    critical = _critical_files_content(document, diagnostics)
    if critical is not None:
        content["critical_files"] = critical
    risks = _risks_content(document, diagnostics)
    if risks:
        content["risks_mitigations"] = risks
    verification = _verification_content(document, diagnostics)
    if verification:
        content["verification_strategy"] = verification
    constraints = _constraints_content(document, diagnostics)
    if constraints is not None:
        content["constraints"] = constraints
    criteria = _acceptance_criteria_content(document, verification_expectations, diagnostics)
    design = design_content(document, criteria, diagnostics)
    if design is not None:
        content["design"] = design
    parallel_plan = _parallel_plan_content(document, steps, diagnostics)
    if parallel_plan is not None:
        content["parallel_plan"] = parallel_plan
    work_units = _work_units_content(document, steps, diagnostics)
    if work_units is None:
        work_units = subplan_units_content(document, steps)
    if work_units is not None:
        content["work_units"] = work_units
    return (content, diagnostics)


def _to_content(document: ParsedDocument) -> Content:
    if document.frontmatter.get("noop") == "true" and (not document.sections):
        return {"noop": True}
    content, _ = _analyze(document)
    if content.get("work_units") == []:
        content.pop("work_units")
    return content


def analyze_plan_document(text: str) -> tuple[Content, list[Diagnostic], list[object]]:
    diagnostics = (
        [Diagnostic(1, None, "PLAN001", "plan contains binary control characters")]
        if any(character in _BINARY_CONTROL_CHARACTERS for character in text)
        else []
    )
    if diagnostics:
        return ({}, diagnostics, [])
    document, _ = parse_markdown_document(text, allow_nested_headings=True)
    try:
        content = _to_content(document)
    except (TypeError, ValueError, RecursionError):
        content = {"steps": []}
    return (content, [], [])


def _parse_plan_text(text: str) -> tuple[Content, list[Diagnostic]]:
    content, diagnostics, _ = analyze_plan_document(text)
    return (content, diagnostics)


PLAN_SPEC = MdArtifactSpec(
    artifact_type=PLAN_ARTIFACT_TYPE,
    required_frontmatter=frozenset(),
    sections={},
    allow_unknown_frontmatter=True,
    allow_unknown_sections=True,
    allow_nested_headings=True,
    to_content=_to_content,
    normalize_content=lambda content: content,
    parse_text=_parse_plan_text,
)
register_spec(PLAN_SPEC)
__all__ = ["PLAN_SPEC", "analyze_plan_document"]

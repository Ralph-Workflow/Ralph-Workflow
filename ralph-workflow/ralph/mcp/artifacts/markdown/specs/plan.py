"""Markdown mapping and validation rules for ``plan`` artifacts.

The plan grammar is JSON-free: prose lives as prose, machine-readable
values live on labeled ``Field: value`` lines, and every cross-referenced
entity carries a stable ID (``S-1`` steps, ``AC-01`` criteria) that other
entities reference by ID only. The mapper translates the document into
the legacy content dict shape and hands it to the canonical
``normalize_plan_artifact_content`` gate, so every pydantic-era guarantee
(required presence, per-step contracts, cycles, caps, shell-invocation
guards) still hard-fails.

Grammar summary (three field kinds, closed key sets per context — see
:mod:`ralph.mcp.artifacts.markdown._fields`):

- ``Field: value`` — single-value field.
- ``Field: a, b`` — inline comma-separated list (IDs, names, enum words).
- ``Field:`` followed by ``- entry`` bullets — list of prose/path entries.

Steps are ``### [S-n] Title`` blocks whose body mixes description prose
with step fields; list-item sections (Scope, Critical Files, Acceptance
Criteria, Risks, Verification, Parallel Plan, Work Units) attach fields
to an item as indented ``  Field: value`` lines.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.markdown._artifact_error import MarkdownArtifactError
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._fields import FieldKind, ParsedFields, parse_fields
from ralph.mcp.artifacts.markdown._parser import parse_markdown_document
from ralph.mcp.artifacts.markdown._spec import (
    Content,
    LenientEnum,
    MdArtifactSpec,
    SectionRule,
    parse_and_validate,
)
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.markdown.specs._plan_design import design_content
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_TYPE, normalize_plan_artifact_content

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem
    from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection

_STEP_ID = re.compile(r"^S-(?P<number>[1-9][0-9]*)$")
_EVIDENCE_ENTRY = re.compile(r"^(?P<kind>[a-z_]+): (?P<ref>\S(?:.*\S)?)$")

_INTENT_VERBS = frozenset(
    {"add", "fix", "refactor", "migrate", "document", "investigate", "improve", "configure", "remove"}
)
_SCOPE_CATEGORIES = frozenset(
    {
        "bugfix", "feature", "refactor", "test", "docs", "infra", "migration", "security",
        "performance", "cleanup", "research", "unknown", "file_change", "prompt", "other",
    }
)
_COVERAGE_AREAS = frozenset(
    {
        "bugfix", "feature", "refactor", "test", "docs", "infra", "security", "performance",
        "migration", "release",
    }
)
_STEP_TYPES = frozenset({"file_change", "action", "research", "verify"})
_EVIDENCE_KINDS = frozenset({"file", "command_output", "test_name"})
_TARGET_ACTIONS = frozenset({"create", "modify", "delete", "read", "reference"})
_PRIORITIES = frozenset({"critical", "high", "medium", "low"})
_SEVERITIES = _PRIORITIES

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


def _required_section(document: ParsedDocument, name: str) -> ParsedSection:
    section = document.section(name)
    if section is None:
        raise ValueError(f"missing required section {name!r}")
    return section


def _reject_section_lines(
    section: ParsedSection, hint: str, diagnostics: list[Diagnostic]
) -> None:
    diagnostics.extend(
        Diagnostic(line.line, section.name, "PLAN020", f"{section.name}: {hint}")
        for line in section.lines
    )


def _item_fields(
    item: ParsedItem,
    table: Mapping[str, FieldKind],
    section: str,
    diagnostics: list[Diagnostic],
) -> ParsedFields:
    return parse_fields(
        item.fields,
        table,
        section=section,
        context=f"item {item.identifier!r}",
        prose_allowed=False,
        diagnostics=diagnostics,
    )


def _coerced_scalar(
    fields: ParsedFields,
    key: str,
    allowed: frozenset[str],
    fallback: str,
    *,
    section: str,
    rule_id: str,
    label: str,
    diagnostics: list[Diagnostic],
) -> str | None:
    """Return a vocabulary scalar, warn-coercing unknown values to ``fallback``."""
    scalar = fields.scalars.get(key)
    if scalar is None:
        return None
    if scalar.text in allowed:
        return scalar.text
    diagnostics.append(
        Diagnostic(
            scalar.line,
            section,
            rule_id,
            f"unknown {label} {scalar.text!r} coerced to {fallback!r}",
            "warning",
        )
    )
    return fallback


def _step_number_map(document: ParsedDocument, diagnostics: list[Diagnostic]) -> dict[str, int]:
    numbers: dict[str, int] = {}
    for block in _required_section(document, "Steps").blocks:
        match = _STEP_ID.fullmatch(block.identifier)
        if match is None:
            diagnostics.append(
                Diagnostic(
                    block.line,
                    "Steps",
                    "PLAN022",
                    f"step ID {block.identifier!r} must use the S-<positive-number> form",
                )
            )
            continue
        numbers[block.identifier] = int(match.group("number"))
    return numbers


def _resolve_step_references(
    entries: list[ParsedLine],
    numbers: Mapping[str, int],
    *,
    section: str,
    context: str,
    diagnostics: list[Diagnostic],
) -> list[int]:
    resolved: list[int] = []
    for entry in entries:
        number = numbers.get(entry.text)
        if number is None:
            diagnostics.append(
                Diagnostic(
                    entry.line,
                    section,
                    "PLAN021",
                    f"{context} references unknown step ID {entry.text!r}",
                )
            )
            continue
        resolved.append(number)
    return resolved


def _summary_content(document: ParsedDocument, diagnostics: list[Diagnostic]) -> Content:
    section = _required_section(document, "Summary")
    fields = parse_fields(
        section.lines,
        _SUMMARY_FIELDS,
        section="Summary",
        context="Summary",
        prose_allowed=True,
        diagnostics=diagnostics,
    )
    summary: Content = {}
    context = "\n".join(line.text for line in fields.prose)
    if context:
        summary["context"] = context
    intent = fields.scalars.get("intent")
    if intent is not None:
        summary["intent"] = intent.text
    coverage: list[str] = []
    for entry in fields.lists.get("coverage", []):
        if entry.text in _COVERAGE_AREAS:
            coverage.append(entry.text)
        else:
            diagnostics.append(
                Diagnostic(
                    entry.line,
                    "Summary",
                    "PLAN008",
                    f"unknown coverage area {entry.text!r} dropped",
                    "warning",
                )
            )
    if coverage:
        summary["coverage_areas"] = coverage
    summary["scope_items"] = _scope_items(document, diagnostics)
    intent_verb = document.frontmatter.get("intent_verb")
    if intent_verb is not None:
        summary["intent_verb"] = intent_verb
    return summary


def _scope_items(document: ParsedDocument, diagnostics: list[Diagnostic]) -> list[Content]:
    section = _required_section(document, "Scope")
    _reject_section_lines(section, "content must be part of a '- [ID] text' item", diagnostics)
    items: list[Content] = []
    for item in section.items:
        fields = _item_fields(item, _SCOPE_ITEM_FIELDS, "Scope", diagnostics)
        scope_item: Content = {"text": item.text}
        category = _coerced_scalar(
            fields,
            "category",
            _SCOPE_CATEGORIES,
            "other",
            section="Scope",
            rule_id="PLAN002",
            label="scope category",
            diagnostics=diagnostics,
        )
        if category is not None:
            scope_item["category"] = category
        count = fields.scalars.get("count")
        if count is not None:
            scope_item["count"] = count.text
        items.append(scope_item)
    return items


def _skills_content(document: ParsedDocument, diagnostics: list[Diagnostic]) -> Content:
    section = _required_section(document, "Skills MCP")
    fields = parse_fields(
        section.lines,
        _SKILLS_FIELDS,
        section="Skills MCP",
        context="Skills MCP",
        prose_allowed=False,
        diagnostics=diagnostics,
    )
    skills = fields.lists.get("skills")
    if skills is None:
        diagnostics.append(
            Diagnostic(
                section.line, "Skills MCP", "PLAN020", "Skills MCP must declare a 'Skills:' field"
            )
        )
    return {
        "skills": [entry.text for entry in skills or []],
        "mcps": [entry.text for entry in fields.lists.get("mcps", [])],
    }


def _target_content(
    entry: ParsedLine, context: str, diagnostics: list[Diagnostic]
) -> Content:
    head, _, rest = entry.text.partition(" ")
    rest = rest.strip()
    if head in _TARGET_ACTIONS and rest:
        return {"path": rest, "action": head}
    diagnostics.append(
        Diagnostic(
            entry.line,
            "Steps",
            "PLAN005",
            f"{context}: unknown target action coerced to 'modify'",
            "warning",
        )
    )
    path = rest if rest and head.isalpha() and head.islower() else entry.text
    return {"path": path, "action": "modify"}


def _evidence_content(
    entry: ParsedLine, context: str, diagnostics: list[Diagnostic]
) -> Content:
    match = _EVIDENCE_ENTRY.fullmatch(entry.text)
    if match is None:
        return {"kind": "file", "ref": entry.text}
    kind = cast("str", match.group("kind"))
    ref = cast("str", match.group("ref"))
    if kind in _EVIDENCE_KINDS:
        return {"kind": kind, "ref": ref}
    diagnostics.append(
        Diagnostic(
            entry.line,
            "Steps",
            "PLAN006",
            f"{context}: unknown evidence kind coerced to 'file'",
            "warning",
        )
    )
    return {"kind": "file", "ref": ref}


def _steps_content(
    document: ParsedDocument,
    numbers: Mapping[str, int],
    diagnostics: list[Diagnostic],
) -> list[Content]:
    section = _required_section(document, "Steps")
    _reject_section_lines(
        section, "content must live inside a '### [S-n] Title' step block", diagnostics
    )
    steps: list[Content] = []
    for block in section.blocks:
        number = numbers.get(block.identifier)
        if number is None:
            continue
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
        else:
            diagnostics.append(
                Diagnostic(
                    block.line, "Steps", "PLAN012", f"{context} must include description prose"
                )
            )
        step_type = _coerced_scalar(
            fields,
            "type",
            _STEP_TYPES,
            "action",
            section="Steps",
            rule_id="PLAN003",
            label="step type",
            diagnostics=diagnostics,
        )
        if step_type is not None:
            step["step_type"] = step_type
        priority = _coerced_scalar(
            fields,
            "priority",
            _PRIORITIES,
            "medium",
            section="Steps",
            rule_id="PLAN004",
            label="priority",
            diagnostics=diagnostics,
        )
        if priority is not None:
            step["priority"] = priority
        files = fields.lists.get("files")
        if files is not None:
            step["targets"] = [_target_content(entry, context, diagnostics) for entry in files]
        depends_on = fields.lists.get("depends on")
        if depends_on is not None:
            step["depends_on"] = _resolve_step_references(
                depends_on, numbers, section="Steps", context=context, diagnostics=diagnostics
            )
        satisfies = fields.lists.get("satisfies")
        if satisfies is not None:
            step["satisfies"] = [entry.text for entry in satisfies]
        for key, name in (("verify", "verify_command"), ("location", "location"), ("rationale", "rationale")):
            scalar = fields.scalars.get(key)
            if scalar is not None:
                step[name] = scalar.text
        evidence = fields.lists.get("evidence")
        if evidence is not None:
            step["expected_evidence"] = [
                _evidence_content(entry, context, diagnostics) for entry in evidence
            ]
        _check_step_contract(step, step_type, block.line, context, diagnostics)
        steps.append(step)
    return steps


def _check_step_contract(
    step: Content,
    step_type: str | None,
    line: int,
    context: str,
    diagnostics: list[Diagnostic],
) -> None:
    effective = step_type or "action"
    targets = step.get("targets")
    if effective == "file_change" and not (isinstance(targets, list) and targets):
        diagnostics.append(
            Diagnostic(
                line,
                "Steps",
                "PLAN010",
                f"{context} is a file_change step and must declare at least one 'Files:' target",
            )
        )
    if effective == "verify" and "verify_command" not in step and "location" not in step:
        diagnostics.append(
            Diagnostic(
                line,
                "Steps",
                "PLAN011",
                f"{context} is a verify step and must declare 'Verify:' or 'Location:'",
            )
        )


def _critical_files_content(document: ParsedDocument, diagnostics: list[Diagnostic]) -> Content:
    section = _required_section(document, "Critical Files")
    _reject_section_lines(section, "content must be part of a '- [ID] path' item", diagnostics)
    primary: list[Content] = []
    reference: list[Content] = []
    for item in section.items:
        fields = _item_fields(item, _CRITICAL_FILE_FIELDS, "Critical Files", diagnostics)
        purpose = fields.scalars.get("purpose")
        if purpose is not None:
            if fields.scalars.get("action") is not None or fields.scalars.get("changes") is not None:
                diagnostics.append(
                    Diagnostic(
                        item.line,
                        "Critical Files",
                        "PLAN020",
                        f"item {item.identifier!r} cannot combine 'Purpose:' with 'Action:' or 'Changes:'",
                    )
                )
                continue
            reference.append({"path": item.text, "purpose": purpose.text})
            continue
        entry: Content = {"path": item.text}
        action = fields.scalars.get("action")
        entry["action"] = action.text if action is not None else "modify"
        changes = fields.scalars.get("changes")
        if changes is not None:
            entry["estimated_changes"] = changes.text
        primary.append(entry)
    if not primary:
        diagnostics.append(
            Diagnostic(
                section.line,
                "Critical Files",
                "PLAN020",
                "Critical Files must include at least one primary file (an item without 'Purpose:')",
            )
        )
    critical: Content = {"primary_files": primary}
    if reference:
        critical["reference_files"] = reference
    return critical


def _constraints_content(document: ParsedDocument, diagnostics: list[Diagnostic]) -> Content | None:
    section = document.section("Constraints")
    if section is None:
        return None
    fields = parse_fields(
        section.lines,
        _CONSTRAINTS_FIELDS,
        section="Constraints",
        context="Constraints",
        prose_allowed=False,
        diagnostics=diagnostics,
    )
    constraints: Content = {}
    for key, name in (("must not break", "must_not_break"), ("must keep working", "must_keep_working")):
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
    document: ParsedDocument, diagnostics: list[Diagnostic]
) -> Content | None:
    section = document.section("Acceptance Criteria")
    if section is None:
        return None
    _reject_section_lines(section, "content must be part of a '- [AC-nn] text' item", diagnostics)
    numbers = _step_number_map(document, [])
    criteria: list[Content] = []
    for item in section.items:
        fields = _item_fields(item, _CRITERION_FIELDS, "Acceptance Criteria", diagnostics)
        criterion: Content = {"id": item.identifier, "description": item.text}
        satisfied_by = fields.lists.get("satisfied by")
        if satisfied_by is not None:
            criterion["satisfied_by_steps"] = _resolve_step_references(
                satisfied_by,
                numbers,
                section="Acceptance Criteria",
                context=f"criterion {item.identifier!r}",
                diagnostics=diagnostics,
            )
        verify = fields.scalars.get("verify")
        if verify is not None:
            criterion["verification_step"] = verify.text
        evidence = fields.scalars.get("evidence")
        if evidence is not None:
            criterion["evidence_path"] = evidence.text
        criteria.append(criterion)
    return {"criteria": criteria}


def _risks_content(document: ParsedDocument, diagnostics: list[Diagnostic]) -> list[Content]:
    section = _required_section(document, "Risks")
    _reject_section_lines(section, "content must be part of a '- [ID] risk' item", diagnostics)
    risks: list[Content] = []
    for item in section.items:
        fields = _item_fields(item, _RISK_FIELDS, "Risks", diagnostics)
        risk: Content = {"risk": item.text}
        mitigation = fields.scalars.get("mitigation")
        if mitigation is None:
            diagnostics.append(
                Diagnostic(
                    item.line,
                    "Risks",
                    "PLAN020",
                    f"risk {item.identifier!r} must declare a 'Mitigation:' field",
                )
            )
        else:
            risk["mitigation"] = mitigation.text
        severity = _coerced_scalar(
            fields,
            "severity",
            _SEVERITIES,
            "medium",
            section="Risks",
            rule_id="PLAN007",
            label="severity",
            diagnostics=diagnostics,
        )
        if severity is not None:
            risk["severity"] = severity
        risks.append(risk)
    return risks


def _verification_content(
    document: ParsedDocument, diagnostics: list[Diagnostic]
) -> list[Content]:
    section = _required_section(document, "Verification")
    _reject_section_lines(section, "content must be part of a '- [ID] command' item", diagnostics)
    entries: list[Content] = []
    for item in section.items:
        fields = _item_fields(item, _VERIFICATION_FIELDS, "Verification", diagnostics)
        entry: Content = {"method": item.text}
        expect = fields.scalars.get("expect")
        if expect is None:
            diagnostics.append(
                Diagnostic(
                    item.line,
                    "Verification",
                    "PLAN020",
                    f"verification item {item.identifier!r} must declare an 'Expect:' field",
                )
            )
        else:
            entry["expected_outcome"] = expect.text
        timeout = fields.scalars.get("timeout")
        if timeout is not None:
            try:
                entry["timeout_seconds"] = int(timeout.text)
            except ValueError:
                diagnostics.append(
                    Diagnostic(
                        timeout.line,
                        "Verification",
                        "PLAN020",
                        "field 'Timeout' must be an integer number of seconds",
                    )
                )
        cwd = fields.scalars.get("cwd")
        if cwd is not None:
            entry["cwd"] = cwd.text
        entries.append(entry)
    return entries


def _parallel_plan_content(
    document: ParsedDocument, diagnostics: list[Diagnostic]
) -> list[Content] | None:
    section = document.section("Parallel Plan")
    if section is None:
        return None
    _reject_section_lines(section, "content must be part of a '- [ID] description' item", diagnostics)
    entries: list[Content] = []
    for item in section.items:
        fields = _item_fields(item, _PARALLEL_FIELDS, "Parallel Plan", diagnostics)
        entries.append(
            {
                "id": item.identifier,
                "description": item.text,
                "edit_area": {
                    "paths": [entry.text for entry in fields.lists.get("paths", [])],
                    "directories": [entry.text for entry in fields.lists.get("directories", [])],
                },
                "depends_on": [entry.text for entry in fields.lists.get("depends on", [])],
            }
        )
    return entries


def _work_units_content(
    document: ParsedDocument, diagnostics: list[Diagnostic]
) -> list[Content] | None:
    section = document.section("Work Units")
    if section is None:
        return None
    _reject_section_lines(section, "content must be part of a '- [unit-id] description' item", diagnostics)
    entries: list[Content] = []
    for item in section.items:
        fields = _item_fields(item, _WORK_UNIT_FIELDS, "Work Units", diagnostics)
        entries.append(
            {
                "unit_id": item.identifier,
                "description": item.text,
                "allowed_directories": [entry.text for entry in fields.lists.get("directories", [])],
                "dependencies": [entry.text for entry in fields.lists.get("depends on", [])],
            }
        )
    return entries


def _analyze(document: ParsedDocument) -> tuple[Content, list[Diagnostic]]:
    """Map the parsed document onto the legacy plan content dict, collecting diagnostics."""
    diagnostics: list[Diagnostic] = []
    type_value = document.frontmatter.get("type")
    if type_value != PLAN_ARTIFACT_TYPE:
        diagnostics.append(
            Diagnostic(
                document.frontmatter_lines.get("type", 1), None, "PLAN020", "type must be 'plan'"
            )
        )
    numbers = _step_number_map(document, diagnostics)
    content: Content = {
        "summary": _summary_content(document, diagnostics),
        "skills_mcp": _skills_content(document, diagnostics),
        "steps": _steps_content(document, numbers, diagnostics),
        "critical_files": _critical_files_content(document, diagnostics),
        "risks_mitigations": _risks_content(document, diagnostics),
        "verification_strategy": _verification_content(document, diagnostics),
    }
    schema_version = document.frontmatter.get("schema_version")
    if schema_version is not None:
        try:
            content["schema_version"] = int(schema_version)
        except ValueError:
            diagnostics.append(
                Diagnostic(
                    document.frontmatter_lines.get("schema_version", 1),
                    None,
                    "PLAN020",
                    "schema_version must be an integer",
                )
            )
    constraints = _constraints_content(document, diagnostics)
    if constraints is not None:
        content["constraints"] = constraints
    criteria = _acceptance_criteria_content(document, diagnostics)
    design = design_content(document, criteria, diagnostics)
    if design is not None:
        content["design"] = design
    parallel_plan = _parallel_plan_content(document, diagnostics)
    if parallel_plan is not None:
        content["parallel_plan"] = parallel_plan
    work_units = _work_units_content(document, diagnostics)
    if work_units is not None:
        content["work_units"] = work_units
    return content, diagnostics


def _to_content(document: ParsedDocument) -> Content:
    content, diagnostics = _analyze(document)
    errors = [diagnostic for diagnostic in diagnostics if diagnostic.severity == "error"]
    if errors:
        raise MarkdownArtifactError(errors)
    return content


def _document_warnings(document: ParsedDocument) -> list[Diagnostic]:
    _, diagnostics = _analyze(document)
    return [diagnostic for diagnostic in diagnostics if diagnostic.severity == "warning"]


def _minimal_noop_variant(
    document: ParsedDocument,
) -> tuple[Content | None, list[Diagnostic]]:
    value = document.frontmatter.get("noop")
    if value is None:
        return None, []
    if value != "true":
        message = "frontmatter 'noop' must be 'true' when present"
    elif document.frontmatter != {"type": "plan", "noop": "true"} or document.sections:
        message = (
            "a no-op plan must contain exactly 'type: plan' and "
            "'noop: true' with no sections"
        )
    else:
        return {"noop": True}, []
    return None, [
        Diagnostic(document.frontmatter_lines["noop"], None, "PLAN023", message)
    ]


def edit_plan_step_markdown(
    text: str,
    action: str,
    step_id: str,
    replacement: str | None = None,
    index: int | None = None,
) -> str:
    """Apply one ID-addressed plan-step edit and return a valid document.

    ``replacement`` is a markdown step block — a ``### [S-n] Title``
    heading followed by its body — not a JSON object. Stable step IDs are
    never renumbered by an edit, so ``Depends on:`` and ``Satisfied by:``
    references survive insert, move, and replace; removing a step that is
    still referenced fails re-validation with a dangling-reference error.
    """
    document, parse_diagnostics = parse_markdown_document(text)
    parse_errors = [d for d in parse_diagnostics if d.severity == "error"]
    if parse_errors:
        raise MarkdownArtifactError(parse_errors)
    steps_section = document.section("Steps")
    if steps_section is None:
        raise ValueError("document has no '## Steps' section")
    lines = text.splitlines()
    section_end = min(
        (section.line - 1 for section in document.sections if section.line > steps_section.line),
        default=len(lines),
    )
    chunks: list[tuple[str, list[str]]] = []
    blocks = steps_section.blocks
    for position, block in enumerate(blocks):
        end = blocks[position + 1].line - 1 if position + 1 < len(blocks) else section_end
        chunk = lines[block.line - 1 : end]
        while chunk and not chunk[-1].strip():
            chunk.pop()
        chunks.append((block.identifier, chunk))
    _apply_step_edit(chunks, action, step_id, replacement, index)
    region: list[str] = [""]
    for _, chunk in chunks:
        region.extend(chunk)
        region.append("")
    lines[steps_section.line : section_end] = region
    edited = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    _, validation = parse_and_validate(edited, PLAN_SPEC)
    validation_errors = [d for d in validation if d.severity == "error"]
    if validation_errors:
        raise MarkdownArtifactError(validation_errors)
    return edited


def _apply_step_edit(
    chunks: list[tuple[str, list[str]]],
    action: str,
    step_id: str,
    replacement: str | None,
    index: int | None,
) -> None:
    """Mutate the ordered (step ID, chunk lines) list according to one edit."""
    positions = {identifier: position for position, (identifier, _) in enumerate(chunks)}
    if action == "insert":
        if replacement is None or step_id in positions:
            raise ValueError("insert requires a new step ID and a replacement block")
        if _STEP_ID.fullmatch(step_id) is None:
            raise ValueError(f"step ID {step_id!r} must use the S-<positive-number> form")
        insert_at = len(chunks) if index is None else _edit_position(index, len(chunks))
        chunks.insert(insert_at, (step_id, _replacement_chunk(replacement, step_id)))
        return
    position = positions.get(step_id)
    if position is None:
        raise ValueError(f"unknown step ID {step_id!r}")
    if action == "replace":
        if replacement is None:
            raise ValueError("replace requires a replacement block")
        chunks[position] = (step_id, _replacement_chunk(replacement, step_id))
    elif action == "remove":
        chunks.pop(position)
    elif action == "move" and index is not None:
        chunks.insert(_edit_position(index, len(chunks) - 1), chunks.pop(position))
    else:
        raise ValueError("action must be replace, insert, remove, or move; move requires index")


def _replacement_chunk(replacement: str, step_id: str) -> list[str]:
    """Validate a replacement step block and return its normalized lines."""
    document, diagnostics = parse_markdown_document("## Steps\n" + replacement)
    errors = [d for d in diagnostics if d.severity == "error"]
    if errors:
        raise MarkdownArtifactError(errors)
    section = document.section("Steps")
    if (
        section is None
        or len(document.sections) != 1
        or len(section.blocks) != 1
        or section.items
        or section.lines
    ):
        raise ValueError("replacement must be a single '### [S-n] Title' step block")
    block = section.blocks[0]
    if block.identifier != step_id:
        raise ValueError(
            f"replacement block ID {block.identifier!r} must match step_id {step_id!r}"
        )
    chunk = replacement.splitlines()
    while chunk and not chunk[0].strip():
        chunk.pop(0)
    while chunk and not chunk[-1].strip():
        chunk.pop()
    return chunk


def _edit_position(index: int, length: int) -> int:
    if not 1 <= index <= length + 1:
        raise ValueError(f"index must be between 1 and {length + 1}")
    return index - 1


PLAN_SPEC = MdArtifactSpec(
    artifact_type=PLAN_ARTIFACT_TYPE,
    required_frontmatter=frozenset({"type"}),
    optional_frontmatter=frozenset({"schema_version", "noop"}),
    lenient_enums={"intent_verb": LenientEnum(_INTENT_VERBS, "add")},
    sections={
        "Summary": SectionRule(allow_body=True, max_items=0),
        "Scope": SectionRule(require_items=True, allow_body=True),
        "Skills MCP": SectionRule(allow_body=True, max_items=0),
        "Steps": SectionRule(allow_body=True, allow_blocks=True, require_blocks=True),
        "Critical Files": SectionRule(require_items=True, allow_body=True),
        "Constraints": SectionRule(required=False, allow_body=True, max_items=0),
        "Design": SectionRule(required=False, allow_body=True, max_items=0),
        "Acceptance Criteria": SectionRule(required=False, require_items=True, allow_body=True),
        "Risks": SectionRule(require_items=True, allow_body=True),
        "Verification": SectionRule(require_items=True, allow_body=True),
        "Parallel Plan": SectionRule(required=False, require_items=True, allow_body=True),
        "Work Units": SectionRule(required=False, require_items=True, allow_body=True),
    },
    to_content=_to_content,
    normalize_content=normalize_plan_artifact_content,
    validate_document=_document_warnings,
    minimal_variant=_minimal_noop_variant,
)

register_spec(PLAN_SPEC)

__all__ = ["PLAN_SPEC", "edit_plan_step_markdown"]

"""Markdown mapping and validation rules for ``plan`` artifacts.

The plan grammar is JSON-free: prose lives as prose, machine-readable
values live on labeled ``Field: value`` lines, and every cross-referenced
entity carries a stable ID (``S-1`` steps, ``AC-01`` criteria) that other
entities reference by ID only. The mapper translates the document into
the canonical plan content mapping and hands it to the
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

Consumed-structure map (what stays strict vs. what is descriptive):

- STRICT — structure a downstream consumer parses out of the plan:
  section presence (the opinionated skeleton), ``### [S-n]`` step IDs
  and their uniqueness/shape (development_result "Plan Items Proven"
  proof IDs cross-reference ``steps[].number`` in
  ``ralph/phases/execution.py``), ``Depends on:`` / ``Satisfied by:``
  step references and cycle checks, step-type contracts (``file_change``
  needs ``Files:``, ``verify`` needs ``Verify:``/``Location:``), and
  ``## Parallel Plan`` / ``## Work Units`` item fields (worker fan-out
  parses unit IDs, edit areas, and dependencies in
  ``ralph/pipeline/work_units.py`` / ``fan_out.py``). The
  shell-invocation guard on verification commands also stays hard.
- TOLERANT — descriptive content nothing downstream machine-parses:
  Scope, Critical Files, Acceptance Criteria, Risks, Verification,
  Skills MCP, and Constraints bodies accept free multi-line prose;
  unknown or malformed field lines there degrade to prose with at most
  a warning (fields required by the canonical model — ``Mitigation:``,
  ``Expect:``, ``Skills:`` — remain required). Design accepts free
  prose and free-form ``Profile:`` / ``Architecture:`` values (the
  documented vocabularies are suggestions, not validation errors).
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.markdown._artifact_error import MarkdownArtifactError
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._fields import FieldKind, ParsedFields, parse_fields
from ralph.mcp.artifacts.markdown._references import validate_unique_ids
from ralph.mcp.artifacts.markdown._spec import (
    Content,
    LenientEnum,
    MdArtifactSpec,
    SectionRule,
)
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.markdown.specs._plan_design import design_content
from ralph.mcp.artifacts.markdown.specs._plan_evaluatability import (
    is_concrete_command,
    is_concrete_verification,
    is_specific_artifact,
)
from ralph.mcp.artifacts.markdown.specs._plan_step_edit import (
    edit_plan_step_markdown as _edit_plan_step_markdown,
)
from ralph.mcp.artifacts.markdown.specs._plan_steps import (
    PLAN_STEP_ID_PATTERN,
    resolve_step_references,
    step_number_map,
)
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_TYPE, normalize_plan_artifact_content

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem
    from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection

_EVIDENCE_ENTRY = re.compile(r"^(?P<kind>[a-z_]+): (?P<ref>\S(?:.*\S)?)$")

_INTENT_VERBS = frozenset(
    {
        "add",
        "fix",
        "refactor",
        "migrate",
        "document",
        "investigate",
        "improve",
        "configure",
        "remove",
    }
)
_SCOPE_CATEGORIES = frozenset(
    {
        "bugfix",
        "feature",
        "refactor",
        "test",
        "docs",
        "infra",
        "migration",
        "security",
        "performance",
        "cleanup",
        "research",
        "unknown",
        "file_change",
        "prompt",
        "other",
    }
)
_COVERAGE_AREAS = frozenset(
    {
        "bugfix",
        "feature",
        "refactor",
        "test",
        "docs",
        "infra",
        "security",
        "performance",
        "migration",
        "release",
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


def _merged_lines(document: ParsedDocument, name: str) -> list[ParsedLine]:
    """Concatenate the body lines of every same-named (repeatable) section."""
    return [line for section in document.sections_named(name) for line in section.lines]


def _merged_items(document: ParsedDocument, name: str) -> list[ParsedItem]:
    """Concatenate the list items of every same-named (repeatable) section."""
    return [item for section in document.sections_named(name) for item in section.items]


def _cross_section_unique_items(
    document: ParsedDocument, name: str, diagnostics: list[Diagnostic]
) -> None:
    """Reject item IDs duplicated ACROSS repeats of one section name.

    Per-section duplicates are already caught by the shared structure
    validator, so this only runs when the section actually repeats.
    """
    if len(document.sections_named(name)) > 1:
        diagnostics.extend(validate_unique_ids(_merged_items(document, name), section=name))


def _reject_section_lines(section: ParsedSection, hint: str, diagnostics: list[Diagnostic]) -> None:
    diagnostics.extend(
        Diagnostic(line.line, section.name, "PLAN020", f"{section.name}: {hint}")
        for line in section.lines
    )


def _item_fields(
    item: ParsedItem,
    table: Mapping[str, FieldKind],
    section: str,
    diagnostics: list[Diagnostic],
    *,
    prose_allowed: bool = True,
) -> ParsedFields:
    return parse_fields(
        item.fields,
        table,
        section=section,
        context=f"item {item.identifier!r}",
        prose_allowed=prose_allowed,
        diagnostics=diagnostics,
    )


def _with_prose(text: str, fields: ParsedFields) -> str:
    """Join an item's lead text with its tolerated multi-line prose continuation."""
    prose = "\n".join(line.text for line in fields.prose)
    return f"{text}\n{prose}" if prose else text


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
    if skills is None:
        diagnostics.append(
            Diagnostic(
                sections[0].line,
                "Skills MCP",
                "PLAN020",
                "Skills MCP must declare a 'Skills:' field",
            )
        )
    return {
        "skills": [entry.text for entry in skills or []],
        "mcps": [entry.text for entry in fields.lists.get("mcps", [])],
    }


def _target_content(entry: ParsedLine, context: str, diagnostics: list[Diagnostic]) -> Content:
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


def _evidence_content(entry: ParsedLine, context: str, diagnostics: list[Diagnostic]) -> Content:
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
            step["depends_on"] = resolve_step_references(
                depends_on, numbers, section="Steps", context=context, diagnostics=diagnostics
            )
        satisfies = fields.lists.get("satisfies")
        if satisfies is not None:
            step["satisfies"] = [entry.text for entry in satisfies]
        for key, name in (
            ("verify", "verify_command"),
            ("location", "location"),
            ("rationale", "rationale"),
        ):
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
        if purpose is not None:
            if (
                fields.scalars.get("action") is not None
                or fields.scalars.get("changes") is not None
            ):
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
                sections[0].line,
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
    document: ParsedDocument, diagnostics: list[Diagnostic]
) -> Content | None:
    items = [
        item
        for section in document.sections
        for item in section.items
        if section.name == "Acceptance Criteria"
        or re.fullmatch(r"AC-[0-9]{2,}", item.identifier) is not None
    ]
    if not items:
        return None
    diagnostics.extend(
        validate_unique_ids(items, section="Acceptance Criteria", case_sensitive=False)
    )
    numbers = step_number_map(document, [])
    criteria: list[Content] = []
    for item in items:
        fields = _item_fields(item, _CRITERION_FIELDS, "Acceptance Criteria", diagnostics)
        criterion: Content = {
            "id": item.identifier,
            "description": _with_prose(item.text, fields),
        }
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
        if verify is not None:
            criterion["verification_step"] = verify.text
            if not is_concrete_command(verify.text):
                diagnostics.append(
                    Diagnostic(
                        verify.line,
                        "Acceptance Criteria",
                        "PLAN020",
                        f"criterion {item.identifier!r} needs a concrete command",
                    )
                )
        evidence = fields.scalars.get("evidence")
        if evidence is not None:
            criterion["evidence_path"] = evidence.text
            if not is_specific_artifact(evidence.text):
                diagnostics.append(
                    Diagnostic(
                        evidence.line,
                        "Acceptance Criteria",
                        "PLAN020",
                        f"criterion {item.identifier!r} needs a concrete file/artifact",
                    )
                )
        if verify is None and evidence is None:
            diagnostics.append(
                Diagnostic(
                    item.line,
                    "Acceptance Criteria",
                    "PLAN020",
                    f"criterion {item.identifier!r} must declare an evaluatable "
                    "'Verify:' command or specific 'Evidence:' file/artifact",
                )
            )
        criteria.append(criterion)
    return {"criteria": criteria}


def _risks_content(document: ParsedDocument, diagnostics: list[Diagnostic]) -> list[Content]:
    risks: list[Content] = []
    for item in _merged_items(document, "Risks"):
        fields = _item_fields(item, _RISK_FIELDS, "Risks", diagnostics)
        risk: Content = {"risk": _with_prose(item.text, fields)}
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


def _verification_content(document: ParsedDocument, diagnostics: list[Diagnostic]) -> list[Content]:
    entries: list[Content] = []
    for item in _merged_items(document, "Verification"):
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
            if not is_concrete_verification(item.text, expect.text):
                diagnostics.append(
                    Diagnostic(
                        expect.line,
                        "Verification",
                        "PLAN020",
                        f"verification item {item.identifier!r} needs a concrete "
                        "command or file/artifact inspection and expected result",
                    )
                )
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
    sections = document.sections_named("Parallel Plan")
    if not sections:
        return None
    for section in sections:
        _reject_section_lines(
            section, "content must be part of a '- [ID] description' item", diagnostics
        )
    _cross_section_unique_items(document, "Parallel Plan", diagnostics)
    entries: list[Content] = []
    for item in _merged_items(document, "Parallel Plan"):
        fields = _item_fields(
            item, _PARALLEL_FIELDS, "Parallel Plan", diagnostics, prose_allowed=False
        )
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
    sections = document.sections_named("Work Units")
    if not sections:
        return None
    for section in sections:
        _reject_section_lines(
            section, "content must be part of a '- [unit-id] description' item", diagnostics
        )
    _cross_section_unique_items(document, "Work Units", diagnostics)
    entries: list[Content] = []
    for section in sections:
        nested_step_ids = [
            block.identifier
            for block in section.blocks
            if PLAN_STEP_ID_PATTERN.fullmatch(block.identifier) is not None
        ]
        owned_step_ids = nested_step_ids if len(section.items) == 1 else []
        for item in section.items:
            fields = _item_fields(
                item, _WORK_UNIT_FIELDS, "Work Units", diagnostics, prose_allowed=False
            )
            entry: Content = {
                "unit_id": item.identifier,
                "description": item.text,
                "allowed_directories": [
                    entry.text for entry in fields.lists.get("directories", [])
                ],
                "dependencies": [entry.text for entry in fields.lists.get("depends on", [])],
            }
            if owned_step_ids:
                entry["step_ids"] = owned_step_ids
            entries.append(entry)
    return entries


def _analyze(document: ParsedDocument) -> tuple[Content, list[Diagnostic]]:
    """Map the parsed document to canonical plan content, collecting diagnostics."""
    diagnostics: list[Diagnostic] = []
    type_value = document.frontmatter.get("type")
    if type_value != PLAN_ARTIFACT_TYPE:
        diagnostics.append(
            Diagnostic(
                document.frontmatter_lines.get("type", 1), None, "PLAN020", "type must be 'plan'"
            )
        )
    numbers = step_number_map(document, diagnostics)
    steps = _steps_content(document, numbers, diagnostics)
    if not steps:
        diagnostics.append(
            Diagnostic(
                1,
                None,
                "PLAN022",
                "plan must contain at least one '### [S-n] Title' step block "
                "(in any section) unless it is a 'noop: true' plan",
            )
        )
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
        message = "a no-op plan must contain exactly 'type: plan' and 'noop: true' with no sections"
    else:
        return {"noop": True}, []
    return None, [Diagnostic(document.frontmatter_lines["noop"], None, "PLAN023", message)]


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
    return _edit_plan_step_markdown(
        text,
        action,
        step_id,
        replacement,
        index,
        spec=PLAN_SPEC,
    )


# Free-shape rule: any body prose, stable-ID list items, and step blocks
# may appear, and the section may repeat (one occurrence per subplan).
_FREE_SECTION_RULE = SectionRule(
    required=False, repeatable=True, allow_body=True, allow_blocks=True, allow_items=True
)

PLAN_SPEC = MdArtifactSpec(
    artifact_type=PLAN_ARTIFACT_TYPE,
    required_frontmatter=frozenset({"type"}),
    optional_frontmatter=frozenset({"schema_version", "noop"}),
    lenient_enums={"intent_verb": LenientEnum(_INTENT_VERBS, "add")},
    sections={
        "Summary": _FREE_SECTION_RULE,
        "Scope": _FREE_SECTION_RULE,
        "Skills MCP": _FREE_SECTION_RULE,
        "Steps": _FREE_SECTION_RULE,
        "Critical Files": _FREE_SECTION_RULE,
        "Constraints": _FREE_SECTION_RULE,
        "Design": _FREE_SECTION_RULE,
        "Acceptance Criteria": _FREE_SECTION_RULE,
        "Risks": _FREE_SECTION_RULE,
        "Verification": _FREE_SECTION_RULE,
        "Parallel Plan": _FREE_SECTION_RULE,
        "Work Units": _FREE_SECTION_RULE,
    },
    unknown_section_rule=_FREE_SECTION_RULE,
    to_content=_to_content,
    normalize_content=normalize_plan_artifact_content,
    validate_document=_document_warnings,
    minimal_variant=_minimal_noop_variant,
)

register_spec(PLAN_SPEC)

__all__ = ["PLAN_SPEC", "edit_plan_step_markdown"]

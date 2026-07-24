"""Markdown mapping and validation rules for ``plan`` artifacts.

The plan grammar is deliberately shape-free. Conventional headings, order,
and prose are recommendations rather than validation requirements. The
pipeline-consumed anchors remain strict: ``type: plan``; document-wide,
parseable, unique ``S-n`` step IDs; resolvable consumed references; parseable
work-unit markers when fan-out is declared; and concrete, evaluatable
acceptance/verification checks.

Grammar summary (three field kinds, closed key sets per context — see
:mod:`ralph.mcp.artifacts.markdown._fields`):

- ``Field: value`` — single-value field.
- ``Field: a, b`` — inline comma-separated list (IDs, names, enum words).
- ``Field:`` followed by ``- entry`` bullets — list of prose/path entries.

Steps are ``### [S-n] Title`` blocks under any ``##`` heading. Work-unit
sections may repeat so each unit can own a complete nested mini-plan.

Consumed-structure map (what stays strict vs. what is descriptive):

- STRICT — structure a downstream consumer parses out of the plan:
  ``### [S-n]`` step IDs and their document-wide uniqueness/shape
  (development_result "Plan Items Proven"
  proof IDs cross-reference ``steps[].number`` in
  ``ralph/phases/execution.py``), ``Depends on:`` / ``Satisfied by:``
  step references and cycle checks, step-type contracts (``file_change``
  needs ``Files:``, ``verify`` needs ``Verify:``/``Location:``), and
  ``## Parallel Plan`` / ``## Work Units`` item fields (worker fan-out
  parses unit IDs, edit areas, and dependencies in
  ``ralph/pipeline/work_units.py`` / ``fan_out.py``). The
  shell-invocation guard on verification commands also stays hard.
- TOLERANT — all headings, ordering, descriptive prose, labels, and
  recommended vocabularies. Unknown sections and repeated conventional
  sections are valid. Descriptive fields are enforced only when their
  content is consumed by one of the strict anchors above.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.markdown._artifact_error import MarkdownArtifactError
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._fields import FieldKind, ParsedFields, parse_fields
from ralph.mcp.artifacts.markdown._references import (
    validate_acyclic_dependencies,
    validate_references,
    validate_unique_ids,
)
from ralph.mcp.artifacts.markdown._spec import (
    Content,
    MdArtifactSpec,
    SectionRule,
)
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.markdown.specs._plan_design import design_content
from ralph.mcp.artifacts.markdown.specs._plan_evaluatability import (
    is_concrete_command,
    is_concrete_verification,
    is_forbidden_shell_invocation,
    is_specific_artifact,
    is_specific_expected_output,
)
from ralph.mcp.artifacts.markdown.specs._plan_step_edit import (
    edit_plan_step_markdown as _edit_plan_step_markdown,
)
from ralph.mcp.artifacts.markdown.specs._plan_steps import (
    resolve_step_references,
    step_number_map,
)
from ralph.mcp.artifacts.markdown.specs._plan_subplans import subplan_units_content
from ralph.mcp.artifacts.markdown.specs._plan_work_units import attach_owned_step_ids
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_TYPE, normalize_plan_artifact_content

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem
    from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine

_EVIDENCE_ENTRY = re.compile(r"^(?P<kind>[a-z_]+): (?P<ref>\S(?:.*\S)?)$")
_ACCEPTANCE_CRITERION_ID_PATTERN = re.compile(r"^AC-[0-9]{2,}$")
_VERIFICATION_ITEM_ID_PATTERN = re.compile(r"^V-[0-9]+$")


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


def _is_acceptance_criterion(item: ParsedItem) -> bool:
    """Return whether ``item`` belongs to the document-wide criterion namespace."""
    return _ACCEPTANCE_CRITERION_ID_PATTERN.fullmatch(item.identifier) is not None


def _verification_items(document: ParsedDocument) -> list[ParsedItem]:
    """Return conventional or semantically identified verification items."""
    return [
        item
        for section in document.sections
        for item in section.items
        if section.name == "Verification"
        or _VERIFICATION_ITEM_ID_PATTERN.fullmatch(item.identifier) is not None
    ]


def _fan_out_unit_items(
    document: ParsedDocument,
    name: str,
    diagnostics: list[Diagnostic],
) -> list[ParsedItem]:
    """Return valid unit markers while failing closed on an exact fan-out heading."""
    unit_items: list[ParsedItem] = []
    for section in document.sections_named(name):
        diagnostics.extend(
            [
                Diagnostic(
                    line.line,
                    name,
                    "PLAN024",
                    f"{name} content must use a '- [unit-ID] description' stable-ID item",
                )
                for line in section.lines
            ]
        )
        section_units = [
            item for item in section.items if not _is_acceptance_criterion(item)
        ]
        if not section_units:
            diagnostics.append(
                Diagnostic(
                    section.line,
                    name,
                    "PLAN024",
                    f"{name} must declare at least one stable-ID unit item",
                )
            )
        unit_items.extend(section_units)
    diagnostics.extend(validate_unique_ids(unit_items, section=name))
    return unit_items


def _item_fields(
    item: ParsedItem,
    table: Mapping[str, FieldKind],
    section: str,
    diagnostics: list[Diagnostic],
    *,
    prose_allowed: bool = True,
    strict_known_fields: bool = False,
) -> ParsedFields:
    first_diagnostic = len(diagnostics)
    fields = parse_fields(
        item.fields,
        table,
        section=section,
        context=f"item {item.identifier!r}",
        prose_allowed=prose_allowed,
        diagnostics=diagnostics,
    )
    if strict_known_fields:
        for index in range(first_diagnostic, len(diagnostics)):
            diagnostic = diagnostics[index]
            if diagnostic.rule_id == "PLAN020" and diagnostic.severity == "warning":
                diagnostics[index] = Diagnostic(
                    diagnostic.line,
                    diagnostic.section,
                    diagnostic.rule_id,
                    diagnostic.message,
                )
    return fields


def _with_prose(text: str, fields: ParsedFields) -> str:
    """Join an item's lead text with its tolerated multi-line prose continuation."""
    prose = "\n".join(line.text for line in fields.prose)
    return f"{text}\n{prose}" if prose else text


def _verification_expectations(document: ParsedDocument) -> dict[str, str]:
    """Index global verification outcomes for legacy exact-command reuse."""
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


def _evidence_content(entry: ParsedLine) -> Content:
    match = _EVIDENCE_ENTRY.fullmatch(entry.text)
    if match is None:
        return {"kind": "file", "ref": entry.text}
    kind = cast("str", match.group("kind"))
    ref = cast("str", match.group("ref"))
    return {"kind": kind, "ref": ref}


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
                _evidence_content(entry) for entry in evidence
            ]
        verify = step.get("verify_command")
        if (
            isinstance(verify, str)
            and "expected_outcome" not in step
            and verify in verification_expectations
        ):
            step["expected_outcome"] = verification_expectations[verify]
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
    verify = step.get("verify_command")
    expected = step.get("expected_outcome")
    if isinstance(verify, str):
        if is_forbidden_shell_invocation(verify):
            diagnostics.append(
                Diagnostic(
                    line,
                    "Steps",
                    "PLAN020",
                    f"{context} verification must not invoke a shell interpreter directly",
                )
            )
        elif not is_concrete_command(verify):
            diagnostics.append(
                Diagnostic(
                    line,
                    "Steps",
                    "PLAN020",
                    f"{context} needs a concrete direct 'Verify:' command",
                )
            )
        if not isinstance(expected, str) or not is_specific_expected_output(expected):
            diagnostics.append(
                Diagnostic(
                    line,
                    "Steps",
                    "PLAN020",
                    f"{context} must pair 'Verify:' with a specific 'Expect:' output",
                )
            )
    location = step.get("location")
    if isinstance(location, str) and not is_specific_artifact(location):
        diagnostics.append(
            Diagnostic(
                line,
                "Steps",
                "PLAN020",
                f"{context} needs a specific file/artifact in 'Location:'",
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
        action = fields.scalars.get("action")
        changes = fields.scalars.get("changes")
        if purpose is not None and action is None and changes is None:
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
        if section.name == "Acceptance Criteria"
        or _is_acceptance_criterion(item)
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
            if not is_concrete_command(verify.text):
                diagnostics.append(
                    Diagnostic(
                        verify.line,
                        "Acceptance Criteria",
                        "PLAN020",
                        f"criterion {item.identifier!r} needs a concrete command",
                    )
                )
            if expected_text is None or not is_specific_expected_output(expected_text):
                diagnostics.append(
                    Diagnostic(
                        verify.line if expect is None else expect.line,
                        "Acceptance Criteria",
                        "PLAN020",
                        f"criterion {item.identifier!r} must pair 'Verify:' with "
                        "a specific 'Expect:' output",
                    )
                )
        elif expect is not None:
            diagnostics.append(
                Diagnostic(
                    expect.line,
                    "Acceptance Criteria",
                    "PLAN020",
                    f"criterion {item.identifier!r} has 'Expect:' without 'Verify:'",
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
            if is_forbidden_shell_invocation(item.text):
                diagnostics.append(
                    Diagnostic(
                        item.line,
                        "Verification",
                        "PLAN020",
                        "verification method must not invoke a shell interpreter directly",
                    )
                )
            elif not is_concrete_verification(item.text, expect.text):
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
    items = _fan_out_unit_items(document, "Parallel Plan", diagnostics)
    entries: list[Content] = []
    for item in items:
        fields = _item_fields(
            item,
            _PARALLEL_FIELDS,
            "Parallel Plan",
            diagnostics,
            strict_known_fields=True,
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
    _validate_unit_graph(
        entries,
        id_key="id",
        dependencies_key="depends_on",
        items=items,
        section="Parallel Plan",
        diagnostics=diagnostics,
    )
    return entries


def _work_units_content(
    document: ParsedDocument,
    steps: list[Content],
    diagnostics: list[Diagnostic],
) -> list[Content] | None:
    sections = document.sections_named("Work Units")
    if not sections:
        return None
    items = _fan_out_unit_items(document, "Work Units", diagnostics)
    entries: list[Content] = []
    for item in items:
        fields = _item_fields(
            item,
            _WORK_UNIT_FIELDS,
            "Work Units",
            diagnostics,
            strict_known_fields=True,
        )
        entry: Content = {
            "unit_id": item.identifier,
            "description": item.text,
            "allowed_directories": [
                entry.text for entry in fields.lists.get("directories", [])
            ],
            "dependencies": [entry.text for entry in fields.lists.get("depends on", [])],
        }
        entries.append(entry)
    attach_owned_step_ids(document, entries, steps)
    _validate_unit_graph(
        entries,
        id_key="unit_id",
        dependencies_key="dependencies",
        items=items,
        section="Work Units",
        diagnostics=diagnostics,
    )
    return entries


def _validate_unit_graph(
    entries: list[Content],
    *,
    id_key: str,
    dependencies_key: str,
    items: list[ParsedItem],
    section: str,
    diagnostics: list[Diagnostic],
) -> None:
    identifiers = [cast("str", entry[id_key]) for entry in entries]
    line_by_id = {
        item.identifier: item.line
        for item in items
    }
    references: dict[str, list[tuple[str, int, str | None]]] = {}
    dependencies: dict[str, list[str]] = {}
    for entry in entries:
        identifier = cast("str", entry[id_key])
        raw_dependencies = cast("list[str]", entry[dependencies_key])
        dependencies[identifier] = raw_dependencies
        for dependency in raw_dependencies:
            references.setdefault(dependency, []).append(
                (identifier, line_by_id.get(identifier, 1), section)
            )
    diagnostics.extend(validate_references(references, identifiers))
    diagnostics.extend(
        validate_acyclic_dependencies(
            dependencies,
            line_by_id=line_by_id,
            section_by_id=dict.fromkeys(identifiers, section),
        )
    )


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
    verification_expectations = _verification_expectations(document)
    steps = _steps_content(
        document,
        numbers,
        verification_expectations,
        diagnostics,
    )
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
    criteria = _acceptance_criteria_content(
        document,
        verification_expectations,
        diagnostics,
    )
    design = design_content(document, criteria, diagnostics)
    if design is not None:
        content["design"] = design
    parallel_plan = _parallel_plan_content(document, diagnostics)
    if parallel_plan is not None:
        content["parallel_plan"] = parallel_plan
    work_units = _work_units_content(document, steps, diagnostics)
    if work_units is None:
        work_units = subplan_units_content(document, steps)
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


# Plan-specific mappers validate consumed fan-out fields and dependency graphs.
_FAN_OUT_SECTION_RULE = SectionRule(
    required=False, repeatable=True, allow_body=True, allow_blocks=True, allow_items=True
)

PLAN_SPEC = MdArtifactSpec(
    artifact_type=PLAN_ARTIFACT_TYPE,
    required_frontmatter=frozenset({"type"}),
    optional_frontmatter=frozenset({"schema_version", "noop"}),
    allow_unknown_frontmatter=True,
    allow_nested_headings=True,
    sections={
        "Parallel Plan": _FAN_OUT_SECTION_RULE,
        "Work Units": _FAN_OUT_SECTION_RULE,
    },
    # Arbitrary chapters stay descriptive; plan mappers validate consumed IDs
    # and edges document-wide without turning narrative lists into requirements.
    allow_unknown_sections=True,
    to_content=_to_content,
    normalize_content=normalize_plan_artifact_content,
    validate_document=_document_warnings,
    minimal_variant=_minimal_noop_variant,
)

register_spec(PLAN_SPEC)

__all__ = ["PLAN_SPEC", "edit_plan_step_markdown"]

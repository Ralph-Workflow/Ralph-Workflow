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
from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.markdown._artifact_error import MarkdownArtifactError
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._fields import FieldKind, ParsedFields, parse_fields
from ralph.mcp.artifacts.markdown._parser import parse_markdown_document
from ralph.mcp.artifacts.markdown._references import (
    validate_acyclic_dependencies,
    validate_references,
    validate_unique_ids,
)
from ralph.mcp.artifacts.markdown._spec import (
    Content,
    MdArtifactSpec,
    SectionRule,
    _normalizer_diagnostic,
    _validate_structure,
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
from ralph.mcp.artifacts.markdown.specs._plan_evidence import evidence_content
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
from ralph.mcp.artifacts.plan.plan_artifact_validation_error import PlanArtifactValidationError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem
    from ralph.mcp.artifacts.markdown._parsed_line import ParsedLine

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


# Consumer phrases reused by error-severity diagnostic messages so the
# blocking rationale is unambiguous. Pipeline consumers in scope:
#
# - _DEVELOPMENT_RESULT_PROOF_CONSUMER: development_result "Plan Items
#   Proven" cross-references step numbers; consumers in
#   ralph/phases/execution.py.
# - _FAN_OUT_CONSUMER: worker fan-out parses ## Work Units / ## Parallel
#   Plan IDs in ralph/pipeline/work_units.py and ralph/pipeline/fan_out.py.
# - _NOOP_ROUTING_CONSUMER: ralph/phases/analysis.py reads 'noop: true' to
#   short-circuit the planning pipeline.
# - _BOUNDED_EXEC_SAFETY_CONSUMER: a shell invocation prefix bypasses the
#   bounded-exec safety policy enforced by every subprocess call site.
# - _SPEC_REGISTRY_CONSUMER: ralph/mcp/artifacts/markdown/registry.py routes
#   the parsed 'type: plan' value to the plan validator; an unknown type
#   means no validator is invoked.
# - _PYDANTIC_SCHEMA_CONSUMER: ralph/mcp/artifacts/plan/_validation.py
#   enforces pydantic field schemas on the canonical plan content dict.
_DEVELOPMENT_RESULT_PROOF_CONSUMER = (
    "blocking because the development_result 'Plan Items Proven' proof in "
    "ralph/phases/execution.py cross-references step numbers"
)
_FAN_OUT_CONSUMER = (
    "blocking because the worker fan-out in ralph/pipeline/work_units.py "
    "(and ralph/pipeline/fan_out.py) parses ## Work Units / ## Parallel "
    "Plan IDs to scope edits and dispatch units"
)
_NOOP_ROUTING_CONSUMER = (
    "blocking because ralph/phases/analysis.py reads 'noop: true' to "
    "short-circuit the planning pipeline"
)
_BOUNDED_EXEC_SAFETY_CONSUMER = (
    "blocking because the bounded-exec safety policy forbids shell "
    "interpreter invocations (subprocess.run is invoked without shell=True)"
)
_SPEC_REGISTRY_CONSUMER = (
    "blocking because the artifact spec registry "
    "(ralph/mcp/artifacts/markdown/registry.py) routes the parsed "
    "'type' value to the plan validator; an unknown type means no validator "
    "is invoked and the artifact is not canonical"
)
_PYDANTIC_SCHEMA_CONSUMER = (
    "blocking because ralph/mcp/artifacts/plan/_validation.py enforces "
    "pydantic field schemas on the canonical plan content dict; an invalid "
    "value prevents normalize_plan_artifact_content from returning"
)
_OVERRIDE_LEDGER_CONSUMER = (
    "blocking because ## Validation Overrides is consumed by the validator's "
    "override ledger; each '- [RULE-ID] reason' entry partitions a matching "
    "advisory diagnostic into the 'overridden' list returned in tool results"
)


def _consumer_rule_message(rule_id: str, what: str, fix: str, consumer: str) -> str:
    """Compose a blocking-severity diagnostic that names its downstream consumer.

    The order is deliberate and load-bearing for the message-convention
    fixture test: agents read the diagnostic in linear order (what →
    consumer → fix) and any rewording must preserve all three parts.
    """
    return f"{what}; {consumer}; resolve by {fix}"


def _advisory_message(rule_id: str, what: str, cost: str, fix: str) -> str:
    """Compose an advisory-severity diagnostic that names the run cost."""
    return f"{what}; the run cost is {cost}; resolve by {fix}"


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
                    _consumer_rule_message(
                        "PLAN024",
                        f"{name} content must use a '- [unit-ID] description' stable-ID item",
                        "rewriting the line as '- [unit-id] description'",
                        _FAN_OUT_CONSUMER,
                    ),
                )
                for line in section.lines
            ]
        )
        section_units = [item for item in section.items if not _is_acceptance_criterion(item)]
        if not section_units:
            diagnostics.append(
                Diagnostic(
                    section.line,
                    name,
                    "PLAN024",
                    _consumer_rule_message(
                        "PLAN024",
                        f"{name} must declare at least one stable-ID unit item",
                        "adding at least one '- [unit-id] description' item",
                        _FAN_OUT_CONSUMER,
                    ),
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
                _advisory_message(
                    "PLAN010",
                    f"{context} is a file_change step and does not declare any 'Files:' target",
                    "the executor will not know which file to edit and the step will stall",
                    "adding at least one 'Files:' entry (modify / create / inspect / coordinate)",
                ),
                "warning",
            )
        )
    if effective == "verify" and "verify_command" not in step and "location" not in step:
        diagnostics.append(
            Diagnostic(
                line,
                "Steps",
                "PLAN011",
                _advisory_message(
                    "PLAN011",
                    f"{context} is a verify step and does not declare 'Verify:' or 'Location:'",
                    "the analysis phase cannot prove completion of this step",
                    "adding either 'Verify: <concrete command>' or 'Location: <file/artifact>'",
                ),
                "warning",
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
                    _consumer_rule_message(
                        "PLAN020",
                        f"{context} verification must not invoke a shell interpreter directly",
                        "rewriting the command as a direct executable path "
                        "(e.g. 'pytest tests/x.py -q' instead of 'bash -c ...')",
                        _BOUNDED_EXEC_SAFETY_CONSUMER,
                    ),
                )
            )
        elif not is_concrete_command(verify):
            diagnostics.append(
                Diagnostic(
                    line,
                    "Steps",
                    "PLAN020",
                    _advisory_message(
                        "PLAN020",
                        f"{context} declares a 'Verify:' command but it is not concrete",
                        "the analysis phase cannot run it as-is and may bounce the plan",
                        "rewriting the command with a real executable and target "
                        "(e.g. 'pytest tests/x.py -q' not 'run the tests')",
                    ),
                    "warning",
                )
            )
        if not isinstance(expected, str) or not is_specific_expected_output(expected):
            diagnostics.append(
                Diagnostic(
                    line,
                    "Steps",
                    "PLAN020",
                    _advisory_message(
                        "PLAN020",
                        f"{context} pairs 'Verify:' with a vague or missing 'Expect:'",
                        "the executor cannot tell success from a hung run without a "
                        "specific expected output",
                        "adding an 'Expect: <observable result>' line that names what "
                        "the command is supposed to print or exit-code",
                    ),
                    "warning",
                )
            )
    location = step.get("location")
    if isinstance(location, str) and not is_specific_artifact(location):
        diagnostics.append(
            Diagnostic(
                line,
                "Steps",
                "PLAN020",
                _advisory_message(
                    "PLAN020",
                    f"{context} has 'Location:' but the value is not a specific file/artifact",
                    "the analysis phase cannot inspect the path to prove completion",
                    "rewriting 'Location:' with a concrete relative path "
                    "(e.g. 'reports/api-proof.json' not 'the report')",
                ),
                "warning",
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
        if section.name == "Acceptance Criteria" or _is_acceptance_criterion(item)
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
                        _advisory_message(
                            "PLAN020",
                            f"criterion {item.identifier!r} declares a 'Verify:' command "
                            "but it is not concrete",
                            "the analysis phase cannot run it as-is and may bounce the plan",
                            "rewriting the command with a real executable and target "
                            "(e.g. 'pytest tests/x.py -q' not 'run the tests')",
                        ),
                        "warning",
                    )
                )
            if expected_text is None or not is_specific_expected_output(expected_text):
                diagnostics.append(
                    Diagnostic(
                        verify.line if expect is None else expect.line,
                        "Acceptance Criteria",
                        "PLAN020",
                        _advisory_message(
                            "PLAN020",
                            f"criterion {item.identifier!r} pairs 'Verify:' with a "
                            "vague or missing 'Expect:'",
                            "the analysis phase cannot tell success from a hung run "
                            "without a specific expected output",
                            "adding 'Expect: <observable result>' that names what the "
                            "command is supposed to print or exit-code",
                        ),
                        "warning",
                    )
                )
        elif expect is not None:
            diagnostics.append(
                Diagnostic(
                    expect.line,
                    "Acceptance Criteria",
                    "PLAN020",
                    _advisory_message(
                        "PLAN020",
                        f"criterion {item.identifier!r} has 'Expect:' without 'Verify:'",
                        "an outcome without a command is unevaluatable",
                        "adding 'Verify: <concrete command>' or removing the 'Expect:' line",
                    ),
                    "warning",
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
                        _advisory_message(
                            "PLAN020",
                            f"criterion {item.identifier!r} has 'Evidence:' but the value "
                            "is not a specific file/artifact",
                            "the analysis phase cannot inspect the path to prove completion",
                            "rewriting 'Evidence:' with a concrete relative path "
                            "(e.g. 'reports/api-proof.json' not 'the report')",
                        ),
                        "warning",
                    )
                )
        if verify is None and evidence is None:
            diagnostics.append(
                Diagnostic(
                    item.line,
                    "Acceptance Criteria",
                    "PLAN020",
                    _advisory_message(
                        "PLAN020",
                        f"criterion {item.identifier!r} has no 'Verify:' command and no "
                        "specific 'Evidence:' file/artifact",
                        "the analysis phase cannot prove the criterion is satisfied",
                        "adding either 'Verify: <concrete command>' with 'Expect:' or "
                        "'Evidence: <concrete file/artifact>'",
                    ),
                    "warning",
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
                    _advisory_message(
                        "PLAN020",
                        f"verification item {item.identifier!r} does not declare an 'Expect:' field",
                        "the analysis phase cannot tell whether the verification passed",
                        "adding 'Expect: <observable result>' that names what the method "
                        "is supposed to print or exit-code",
                    ),
                    "warning",
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
                        _consumer_rule_message(
                            "PLAN020",
                            "verification method must not invoke a shell interpreter directly",
                            "rewriting the method as a direct executable path "
                            "(e.g. 'pytest tests/x.py -q' instead of 'bash -c ...')",
                            _BOUNDED_EXEC_SAFETY_CONSUMER,
                        ),
                    )
                )
            elif not is_concrete_verification(item.text, expect.text):
                diagnostics.append(
                    Diagnostic(
                        expect.line,
                        "Verification",
                        "PLAN020",
                        _advisory_message(
                            "PLAN020",
                            f"verification item {item.identifier!r} is not a concrete command "
                            "or file/artifact inspection and expected result",
                            "the analysis phase cannot run it as-is and may bounce the plan",
                            "rewriting the method with a real executable and target "
                            "(e.g. 'pytest tests/x.py -q' not 'run the tests')",
                        ),
                        "warning",
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
                        _advisory_message(
                            "PLAN020",
                            "field 'Timeout' is not an integer number of seconds",
                            "the runtime cannot bound the verification and the executor "
                            "may hang without a usable timeout",
                            "rewriting 'Timeout:' with an integer number of seconds",
                        ),
                        "warning",
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
            "allowed_directories": [entry.text for entry in fields.lists.get("directories", [])],
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
    line_by_id = {item.identifier: item.line for item in items}
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
                document.frontmatter_lines.get("type", 1),
                None,
                "PLAN020",
                _consumer_rule_message(
                    "PLAN020",
                    f"frontmatter 'type' must be 'plan' (got {type_value!r})",
                    "rewriting the frontmatter 'type:' field to the literal value 'plan'",
                    _SPEC_REGISTRY_CONSUMER,
                ),
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
                _consumer_rule_message(
                    "PLAN022",
                    "plan must contain at least one '### [S-n] Title' step block",
                    "adding at least one '### [S-n] Title' step block in any section, or "
                    "marking the plan as 'noop: true' in the frontmatter",
                    _DEVELOPMENT_RESULT_PROOF_CONSUMER,
                ),
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
                    _consumer_rule_message(
                        "PLAN020",
                        "frontmatter 'schema_version' must be an integer",
                        "rewriting 'schema_version:' with an integer value (e.g. '1')",
                        _PYDANTIC_SCHEMA_CONSUMER,
                    ),
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


def analyze_plan_document(
    text: str,
) -> tuple[Content, list[Diagnostic], list[_OverrideMatch]]:
    """Parse and analyze a plan markdown document, returning the override ledger.

    Companion to :func:`parse_and_validate` that also surfaces the validation
    override ledger. Tool handlers that need ``counts`` and ``overridden`` in
    the response payload use this entry point. The diagnostic list reflects
    post-override state: matched advisory diagnostics are removed and the
    recorded reason appears in ``overridden`` instead. Stale and error-targeted
    overrides are surfaced back into the diagnostic list as PLAN025/PLAN026.
    """
    document, _ = parse_markdown_document(text, allow_nested_headings=PLAN_SPEC.allow_nested_headings)
    diagnostics, overridden, minimal_content = _collect_diagnostics_with_overrides(document)
    content: Content
    if any(diagnostic.severity == "error" for diagnostic in diagnostics):
        content = {}
    elif minimal_content is not None:
        # The minimal variant (e.g. noop: true with no sections) already
        # returned a valid canonical payload; use it instead of running the
        # full mapper, which would raise PLAN022 for a step-less document.
        content = minimal_content
    else:
        try:
            content = PLAN_SPEC.to_content(document)
            content = PLAN_SPEC.normalize_content(content)
        except MarkdownArtifactError as exc:
            for diagnostic in exc.diagnostics:
                if diagnostic not in diagnostics:
                    diagnostics.append(diagnostic)
            content = {}
        except PlanArtifactValidationError as exc:
            # Canonical pydantic validation rejects the mapped content even
            # though the markdown side has no blocking diagnostic. Surface it
            # as a SPEC010 error so the tool payload reflects the failure.
            diagnostics.append(_normalizer_diagnostic(document, str(exc)))
            content = {}
    return content, diagnostics, overridden


def _collect_diagnostics_with_overrides(
    document: ParsedDocument,
) -> tuple[list[Diagnostic], list[_OverrideMatch], Content | None]:
    """Collect all plan-spec diagnostics, apply the override ledger, and return both.

    Re-runs the same diagnostic surface :func:`parse_and_validate` collects
    (structure validation + ``_analyze``) so callers see the same finding list
    regardless of which entry point they use. Errors are critical for the
    override ledger because an override targeting an error rule must be
    flagged as PLAN026, not PLAN025, so we cannot use the warnings-only
    helper ``_document_warnings`` here. The returned ``minimal_content`` is
    the canonical payload from the minimal variant (e.g. ``{"noop": True}``)
    when it produced a valid one; callers use it instead of running the
    full mapper, which would raise PLAN022 for a step-less document.
    """
    diagnostics: list[Diagnostic] = []
    minimal_content: Content | None = None
    if PLAN_SPEC.minimal_variant is not None:
        minimal_content, variant_diagnostics = PLAN_SPEC.minimal_variant(document)
        diagnostics.extend(variant_diagnostics)
    diagnostics.extend(
        _validate_structure(
            document,
            document_text_for_validation(document),
            PLAN_SPEC,
            require_sections=minimal_content is None,
        )
    )
    if (
        minimal_content is None
        and not any(diagnostic.severity == "error" for diagnostic in diagnostics)
    ):
        # _analyze emits errors and warnings together; we need both for the
        # override ledger's error-vs-stale distinction.
        _content, analyze_diagnostics = _analyze(document)
        diagnostics.extend(analyze_diagnostics)
    overridden, _stale = _apply_validation_overrides(document, diagnostics)
    return diagnostics, overridden, minimal_content


def document_text_for_validation(document: ParsedDocument) -> str:
    """Source text representation suitable for re-running structure validation.

    Used by :func:`_collect_diagnostics_with_overrides` to mirror the size
    limit and frontmatter checks ``parse_and_validate`` performs. The parsed
    frontmatter is sufficient for the structural checks we need.
    """
    return _reconstructed_text(document)


def _reconstructed_text(document: ParsedDocument) -> str:
    """Render a minimal text representation of the document for size validation."""
    parts: list[str] = []
    if document.frontmatter:
        parts.append("---")
        for key, value in document.frontmatter.items():
            parts.append(f"{key}: {value}")
        parts.append("---")
    return "\n".join(parts)


# Result of the override ledger applied to collected diagnostics. Both lists
# are produced by ``_apply_validation_overrides``; ``overridden`` carries the
# diagnostic plus the recorded reason; ``stale`` carries the override items
# whose rule (and optional section) matched no diagnostic in this document.
@dataclass(frozen=True)
class _OverrideMatch:
    """One recorded override paired with the diagnostic it suppressed."""

    rule_id: str
    section: str | None
    reason: str
    diagnostic: Diagnostic


@dataclass(frozen=True)
class _OverrideOutcome:
    """The full override-ledger result, split by what it consumed."""

    overridden: tuple[_OverrideMatch, ...]
    stale: tuple[tuple[str, str | None, str], ...]


_OVERRIDE_ITEM_PATTERN = re.compile(
    r"^\s*-\s*\[(?P<rule>[A-Z][A-Z0-9_-]*)\]"
    r"(?:\s+Where:\s*(?P<section>[^\s\[][^\[]*?))?"
    r"\s+(?P<reason>\S.*)$"
)


def _parse_override_items(
    document: ParsedDocument, diagnostics: list[Diagnostic]
) -> list[tuple[str, str | None, str]]:
    """Parse the ``## Validation Overrides`` section, fail closed on bad prose.

    The shared parser extracts the ``[RULE-ID]`` prefix as the item
    identifier and leaves only the post-ID prose in ``item.text``. We
    reconstruct the canonical ``- [RULE-ID] reason`` form here so the
    override grammar can match it.
    """
    entries: list[tuple[str, str | None, str]] = []
    for section in document.sections_named("Validation Overrides"):
        if section.lines:
            diagnostics.append(
                Diagnostic(
                    section.line,
                    "Validation Overrides",
                    "PLAN025",
                    _consumer_rule_message(
                        "PLAN025",
                        "## Validation Overrides allows only '- [RULE-ID] reason' list items",
                        "rewriting the section so every line is '- [RULE-ID] reason' with "
                        "an optional 'Where: <section>' label narrowing the match",
                        _OVERRIDE_LEDGER_CONSUMER,
                    ),
                )
            )
        for item in section.items:
            full_text = f"- [{item.identifier}] {item.text}"
            match = _OVERRIDE_ITEM_PATTERN.match(full_text)
            if match is None:
                diagnostics.append(
                    Diagnostic(
                        item.line,
                        "Validation Overrides",
                        "PLAN025",
                        _consumer_rule_message(
                            "PLAN025",
                            f"override item must be '- [RULE-ID] reason' "
                            f"(optionally 'Where: <section>'); got {full_text!r}",
                            "rewriting the line as '- [RULE-ID] reason' with an "
                            "optional 'Where: <section>' label",
                            _OVERRIDE_LEDGER_CONSUMER,
                        ),
                    )
                )
                continue
            entries.append(
                (
                    cast("str", match.group("rule")),
                    cast("str", match.group("section")).strip()
                    if match.group("section")
                    else None,
                    cast("str", match.group("reason")).strip(),
                )
            )
    return entries


# Branch count exceeds the global 14-cap because each override
# classification (matched, error-targeted, stale) needs a distinct
# branch; splitting the function across helpers would scatter a single
# ledger design into 4-5 helpers with worse readability. The complexity
# is bounded by the ``entries`` list length and is exercised fully by
# ``tests/mcp/test_md_plan_advisory.py``.
# ruff: noqa: PLR0912
def _apply_validation_overrides(
    document: ParsedDocument,
    diagnostics: list[Diagnostic],
) -> tuple[list[_OverrideMatch], list[tuple[str, str | None, str]]]:
    """Move matched non-error diagnostics into an ``overridden`` list and flag stale entries.

    Errors are never overridable; an override targeting a rule that fired
    only as error stays visible as PLAN026. Overrides with no matching
    diagnostic surface as PLAN025 info.
    """
    entries = _parse_override_items(document, diagnostics)
    if not entries:
        return [], []
    overridden: list[_OverrideMatch] = []
    used_entries: set[int] = set()
    seen_rule_errors: dict[str, list[Diagnostic]] = {}
    for diagnostic in diagnostics:
        if diagnostic.severity == "error":
            seen_rule_errors.setdefault(diagnostic.rule_id, []).append(diagnostic)
    # Track which diagnostics get consumed by an override so we can remove
    # them from the diagnostic list (they live in ``overridden`` instead).
    consumed_diagnostics: list[Diagnostic] = []
    for index, (rule_id, section, reason) in enumerate(entries):
        if seen_rule_errors.get(rule_id):
            # Errors are never overridable; PLAN026 is emitted below for
            # every override that targets an error rule.
            continue
        matches: list[Diagnostic] = []
        for diagnostic in diagnostics:
            if diagnostic.severity == "error":
                continue
            if diagnostic.rule_id != rule_id:
                continue
            if section is not None and diagnostic.section != section:
                continue
            matches.append(diagnostic)
        if not matches:
            continue
        for matched in matches:
            overridden.append(
                _OverrideMatch(
                    rule_id=rule_id,
                    section=section,
                    reason=reason,
                    diagnostic=matched,
                )
            )
            consumed_diagnostics.append(matched)
        used_entries.add(index)
    if consumed_diagnostics:
        # Remove the suppressed diagnostics from the list callers see, so a
        # PLAN010 warning with a recorded override does not also surface as
        # a live warning in the tool payload.
        diagnostics[:] = [
            diagnostic
            for diagnostic in diagnostics
            if diagnostic not in consumed_diagnostics
        ]
    for index, (rule_id, section, _reason) in enumerate(entries):
        if index in used_entries:
            continue
        if seen_rule_errors.get(rule_id):
            target_section = (
                seen_rule_errors[rule_id][0].section if seen_rule_errors[rule_id] else None
            )
            diagnostics.append(
                Diagnostic(
                    1,
                    target_section,
                    "PLAN026",
                    _consumer_rule_message(
                        "PLAN026",
                        f"override targets {rule_id!r}, which fired as an error "
                        "(errors are not overridable)",
                        "fixing the underlying error or removing this override entry",
                        _OVERRIDE_LEDGER_CONSUMER,
                    ),
                    "warning",
                )
            )
        else:
            diagnostics.append(
                Diagnostic(
                    1,
                    section,
                    "PLAN025",
                    _consumer_rule_message(
                        "PLAN025",
                        f"override targets {rule_id!r} but no diagnostic with that "
                        "rule_id (and matching section) fired in this document",
                        "removing the stale override entry or aligning it with a rule "
                        "that actually fired",
                        _OVERRIDE_LEDGER_CONSUMER,
                    ),
                    "info",
                )
            )
    stale: list[tuple[str, str | None, str]] = [
        entry
        for index, entry in enumerate(entries)
        if index not in used_entries and not seen_rule_errors.get(entry[0])
    ]
    return overridden, stale


def _minimal_noop_variant(
    document: ParsedDocument,
) -> tuple[Content | None, list[Diagnostic]]:
    value = document.frontmatter.get("noop")
    if value is None:
        return None, []
    if value != "true":
        message = _consumer_rule_message(
            "PLAN023",
            "frontmatter 'noop' must be the literal value 'true' when present",
            "setting 'noop: true' (or removing the field for an active plan)",
            _NOOP_ROUTING_CONSUMER,
        )
    elif document.frontmatter != {"type": "plan", "noop": "true"} or document.sections:
        message = _consumer_rule_message(
            "PLAN023",
            "a no-op plan must contain exactly 'type: plan' and 'noop: true' with no sections",
            "removing every section and any extra frontmatter so the no-op payload is exactly "
            "{'type: plan', 'noop: true'}",
            _NOOP_ROUTING_CONSUMER,
        )
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

__all__ = ["PLAN_SPEC", "analyze_plan_document", "edit_plan_step_markdown"]

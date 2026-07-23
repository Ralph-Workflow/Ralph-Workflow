"""Markdown mapping and validation rules for ``plan`` artifacts.

List-item text is a JSON object.  Stable item IDs remain markdown syntax;
step IDs (``S-01``) are translated to the integer references required by the
existing plan model before it performs the canonical validation.
"""

from __future__ import annotations

import json
import re
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.markdown._artifact_error import MarkdownArtifactError
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._parser import parse_markdown_document
from ralph.mcp.artifacts.markdown._spec import (
    Content,
    LenientEnum,
    MdArtifactSpec,
    SectionRule,
    parse_and_validate,
)
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.plan import PLAN_ARTIFACT_TYPE, normalize_plan_artifact_content

if TYPE_CHECKING:
    from collections.abc import Mapping

    from ralph.mcp.artifacts.markdown._document import ParsedDocument, ParsedItem

_STEP_ID = re.compile(r"^S-(?P<number>[1-9][0-9]*)$")
_INTENT_VERBS = frozenset(
    {"add", "fix", "refactor", "migrate", "document", "investigate", "improve", "configure", "remove"}
)
_SCOPE_CATEGORIES = frozenset(
    {
        "bugfix", "feature", "refactor", "test", "docs", "infra", "migration", "security",
        "performance", "cleanup", "research", "unknown", "file_change", "prompt", "other",
    }
)
_STEP_TYPES = frozenset({"file_change", "action", "research", "verify"})
_EVIDENCE_KINDS = frozenset({"file", "command_output", "test_name"})
_TARGET_ACTIONS = frozenset({"create", "modify", "delete", "read", "reference"})
_PRIORITIES = frozenset({"critical", "high", "medium", "low"})
_SEVERITIES = _PRIORITIES


def _section_items(document: ParsedDocument, name: str) -> tuple[ParsedItem, ...]:
    section = document.section(name)
    if section is None:
        raise ValueError(f"missing required section {name!r}")
    return section.items


def _object(item: ParsedItem) -> dict[str, object]:
    try:
        value: object = json.loads(item.text)
    except json.JSONDecodeError as exc:
        raise ValueError(f"{item.identifier} must contain a JSON object") from exc
    if not isinstance(value, dict):
        raise ValueError(f"{item.identifier} must contain a JSON object")
    return cast("dict[str, object]", value)


def _one_object(document: ParsedDocument, name: str) -> dict[str, object]:
    items = _section_items(document, name)
    if len(items) != 1:
        raise ValueError(f"{name} must contain exactly one item")
    return _object(items[0])


def _objects(document: ParsedDocument, name: str) -> list[dict[str, object]]:
    return [_object(item) for item in _section_items(document, name)]


def _optional_one_object(document: ParsedDocument, name: str) -> dict[str, object] | None:
    section = document.section(name)
    if section is None:
        return None
    if len(section.items) != 1:
        raise ValueError(f"{name} must contain exactly one item")
    return _object(section.items[0])


def _optional_objects(document: ParsedDocument, name: str) -> list[dict[str, object]]:
    section = document.section(name)
    return [] if section is None else [_object(item) for item in section.items]


def _step_number(identifier: str) -> int:
    match = _STEP_ID.fullmatch(identifier)
    if match is None:
        raise ValueError(f"step ID {identifier!r} must use the S-<positive-number> form")
    return int(match.group("number"))


def _step_content(document: ParsedDocument) -> list[dict[str, object]]:
    steps: list[dict[str, object]] = []
    numbers_by_id: dict[str, int] = {}
    for item in _section_items(document, "Steps"):
        number = _step_number(item.identifier)
        numbers_by_id[item.identifier] = number
        step = _object(item)
        step["number"] = number
        steps.append(step)
    for step in steps:
        dependencies = step.get("depends_on", [])
        if not isinstance(dependencies, list) or not all(isinstance(value, str) for value in dependencies):
            raise ValueError("Steps.depends_on must be a list of step IDs")
        step_ids = cast("list[str]", dependencies)
        unknown = next((value for value in step_ids if value not in numbers_by_id), None)
        if unknown is not None:
            raise ValueError(f"Steps references unknown step ID {unknown!r}")
        step["depends_on"] = [numbers_by_id[value] for value in step_ids]
    return steps


def _coerce_plan_vocabulary(content: Content) -> Content:
    summary = cast("dict[str, object]", content["summary"])
    intent_verb = summary.get("intent_verb")
    if isinstance(intent_verb, str) and intent_verb not in _INTENT_VERBS:
        summary["intent_verb"] = "add"
    for scope_item in cast("list[dict[str, object]]", summary.get("scope_items", [])):
        if scope_item.get("category") not in _SCOPE_CATEGORIES:
            scope_item["category"] = "other"
    for step in cast("list[dict[str, object]]", content["steps"]):
        if step.get("step_type") not in _STEP_TYPES:
            step["step_type"] = "action"
        if step.get("priority") is not None and step.get("priority") not in _PRIORITIES:
            step["priority"] = "medium"
        for target in cast("list[dict[str, object]]", step.get("targets", [])):
            if target.get("action") not in _TARGET_ACTIONS:
                target["action"] = "modify"
        for evidence in cast("list[dict[str, object]]", step.get("expected_evidence", [])):
            if evidence.get("kind") not in _EVIDENCE_KINDS:
                evidence["kind"] = "file"
    for risk in cast("list[dict[str, object]]", content["risks_mitigations"]):
        if risk.get("severity") is not None and risk.get("severity") not in _SEVERITIES:
            risk["severity"] = "medium"
    return content


def _to_content(document: ParsedDocument) -> Content:
    if document.frontmatter.get("type") != PLAN_ARTIFACT_TYPE:
        raise ValueError("type must be 'plan'")
    summary = _one_object(document, "Summary")
    if "intent_verb" in document.frontmatter:
        summary["intent_verb"] = document.frontmatter["intent_verb"]
    content: Content = {
        "summary": summary,
        "skills_mcp": _one_object(document, "Skills MCP"),
        "steps": _step_content(document),
        "critical_files": _one_object(document, "Critical Files"),
        "risks_mitigations": _objects(document, "Risks Mitigations"),
        "verification_strategy": _objects(document, "Verification"),
    }
    schema_version = document.frontmatter.get("schema_version")
    if schema_version is not None:
        try:
            content["schema_version"] = int(schema_version)
        except ValueError as exc:
            raise ValueError("schema_version must be an integer") from exc
    for section_name, field_name in (
        ("Constraints", "constraints"),
        ("Design", "design"),
    ):
        value = _optional_one_object(document, section_name)
        if value is not None:
            content[field_name] = value
    for section_name, field_name in (("Parallel Plan", "parallel_plan"), ("Work Units", "work_units")):
        values = _optional_objects(document, section_name)
        if values:
            content[field_name] = values
    return _coerce_plan_vocabulary(content)


def _vocabulary_warnings(document: ParsedDocument) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    intent_verb = document.frontmatter.get("intent_verb")
    if intent_verb is not None and intent_verb not in _INTENT_VERBS:
        diagnostics.append(
            Diagnostic(
                document.frontmatter_lines["intent_verb"],
                None,
                "PLAN001",
                "unknown intent_verb coerced to 'add'",
                "warning",
            )
        )
    for section_name in ("Summary", "Steps", "Risks Mitigations"):
        section = document.section(section_name)
        if section is None:
            continue
        for item in section.items:
            try:
                value = _object(item)
            except ValueError:
                continue
            if section_name == "Summary":
                diagnostics.extend(
                    Diagnostic(
                        item.line,
                        section_name,
                        "PLAN002",
                        "unknown scope category coerced to 'other'",
                        "warning",
                    )
                    for scope_item in cast("list[Mapping[str, object]]", value.get("scope_items", []))
                    if scope_item.get("category") not in _SCOPE_CATEGORIES
                )
            elif section_name == "Steps":
                diagnostics.extend(_step_vocabulary_warnings(item, value))
            elif value.get("severity") is not None and value.get("severity") not in _SEVERITIES:
                diagnostics.append(Diagnostic(item.line, section_name, "PLAN007", "unknown severity coerced to 'medium'", "warning"))
    return diagnostics



def _step_vocabulary_warnings(item: ParsedItem, value: Mapping[str, object]) -> list[Diagnostic]:
    warnings: list[Diagnostic] = []
    if value.get("step_type") not in _STEP_TYPES:
        warnings.append(Diagnostic(item.line, "Steps", "PLAN003", "unknown step_type coerced to 'action'", "warning"))
    if value.get("priority") is not None and value.get("priority") not in _PRIORITIES:
        warnings.append(Diagnostic(item.line, "Steps", "PLAN004", "unknown priority coerced to 'medium'", "warning"))
    warnings.extend(
        Diagnostic(item.line, "Steps", "PLAN005", "unknown target action coerced to 'modify'", "warning")
        for target in cast("list[Mapping[str, object]]", value.get("targets", []))
        if target.get("action") not in _TARGET_ACTIONS
    )
    warnings.extend(
        Diagnostic(item.line, "Steps", "PLAN006", "unknown evidence kind coerced to 'file'", "warning")
        for evidence in cast("list[Mapping[str, object]]", value.get("expected_evidence", []))
        if evidence.get("kind") not in _EVIDENCE_KINDS
    )
    return warnings


def edit_plan_step_markdown(
    text: str,
    action: str,
    step_id: str,
    replacement: dict[str, object] | None = None,
    index: int | None = None,
) -> str:
    """Apply one ID-addressed plan-step edit and return a valid document."""
    document, diagnostics = parse_markdown_document(text)
    if diagnostics:
        raise MarkdownArtifactError(diagnostics)
    items = _section_items(document, "Steps")
    steps = [(item.identifier, _object(item)) for item in items]
    positions = {identifier: position for position, (identifier, _) in enumerate(steps)}
    if action == "insert":
        if step_id in positions or replacement is None:
            raise ValueError("insert requires a new step ID and replacement")
        _step_number(step_id)
        insert_at = len(steps) if index is None else _edit_position(index, len(steps))
        steps.insert(insert_at, (step_id, dict(replacement)))
    else:
        position = positions.get(step_id)
        if position is None:
            raise ValueError(f"unknown step ID {step_id!r}")
        if action == "replace":
            if replacement is None:
                raise ValueError("replace requires a replacement")
            steps[position] = (step_id, dict(replacement))
        elif action == "remove":
            steps.pop(position)
        elif action == "move" and index is not None:
            steps.insert(_edit_position(index, len(steps) - 1), steps.pop(position))
        else:
            raise ValueError("action must be replace, insert, remove, or move; move requires index")
    ids = {identifier: f"S-{number}" for number, (identifier, _) in enumerate(steps, 1)}
    rendered = []
    for identifier, content in steps:
        dependencies = content.get("depends_on", [])
        if not isinstance(dependencies, list) or not all(isinstance(value, str) for value in dependencies):
            raise ValueError("Steps.depends_on must be a list of step IDs")
        content["depends_on"] = [ids.get(value, value) for value in cast("list[str]", dependencies)]
        rendered.append(f"- [{ids[identifier]}] {json.dumps(content, separators=(',', ':'))}")
    lines = text.splitlines()
    steps_section = document.section("Steps")
    assert steps_section is not None
    end = min((section.line - 1 for section in document.sections if section.line > steps_section.line), default=len(lines))
    lines[steps_section.line:end] = rendered
    edited = "\n".join(lines) + ("\n" if text.endswith("\n") else "")
    _, validation = parse_and_validate(edited, PLAN_SPEC)
    errors = [diagnostic for diagnostic in validation if diagnostic.severity == "error"]
    if errors:
        raise MarkdownArtifactError(errors)
    return edited


def _edit_position(index: int, length: int) -> int:
    if not 1 <= index <= length + 1:
        raise ValueError(f"index must be between 1 and {length + 1}")
    return index - 1


PLAN_SPEC = MdArtifactSpec(
    artifact_type=PLAN_ARTIFACT_TYPE,
    required_frontmatter=frozenset({"type"}),
    optional_frontmatter=frozenset({"schema_version"}),
    lenient_enums={"intent_verb": LenientEnum(_INTENT_VERBS, "add")},
    sections={
        "Summary": SectionRule(require_items=True, max_items=1),
        "Skills MCP": SectionRule(require_items=True, max_items=1),
        "Steps": SectionRule(require_items=True),
        "Critical Files": SectionRule(require_items=True, max_items=1),
        "Constraints": SectionRule(required=False, require_items=True, max_items=1),
        "Design": SectionRule(required=False, require_items=True, max_items=1),
        "Risks Mitigations": SectionRule(require_items=True),
        "Verification": SectionRule(require_items=True),
        "Parallel Plan": SectionRule(required=False, require_items=True),
        "Work Units": SectionRule(required=False, require_items=True),
    },
    to_content=_to_content,
    normalize_content=normalize_plan_artifact_content,
    validate_document=_vocabulary_warnings,
)

register_spec(PLAN_SPEC)

__all__ = ["PLAN_SPEC", "edit_plan_step_markdown"]

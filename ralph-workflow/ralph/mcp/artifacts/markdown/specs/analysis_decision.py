"""Markdown specs for planning, development, review, and policy decisions."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown import MdArtifactSpec, SectionRule
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._frontmatter_vocabulary import FrontmatterVocabulary
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.typed_artifacts import normalize_analysis_decision_content

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument

_ANALYSIS_TYPES = (
    "planning_analysis_decision",
    "development_analysis_decision",
    "review_analysis_decision",
    "policy_remediation_analysis_decision",
)
_VERIFICATION_TYPES = frozenset(_ANALYSIS_TYPES) - {"review_analysis_decision"}
_STATUSES = ("completed", "request_changes", "failed")
_FINDING_TARGET_PATTERN = re.compile(r"(?:Step:\s*)?\[(S-[1-9][0-9]*)\]|Plan-level:", re.IGNORECASE)
_STEP_REFERENCE_PATTERN = re.compile(r"Step:\s*\[(S-[1-9][0-9]*)\]")
_REQUIRED_FINDING_FIELDS = ("Criterion:", "Expected observation:", "Verdict:", "Evidence:", "Location:")
_COMPLETED_EVIDENCE_MARKER = "Evidence:"
_NOT_EVALUABLE_MARKER = "verdict: not evaluable"


def _item_texts(document: ParsedDocument, section_name: str) -> list[str]:
    section = document.section(section_name)
    if section is None:
        return []
    return [item.text for item in section.items]


def _finding_target(text: str) -> str | None:
    if "Plan-level:" in text:
        return "plan-level"
    match = _STEP_REFERENCE_PATTERN.search(text)
    return None if match is None else match.group(1)


def _to_content(document: ParsedDocument) -> dict[str, object]:
    shortfall_section = document.section("What Came Up Short")
    shortfall_items = () if shortfall_section is None else shortfall_section.items
    how_to_fix = document.section("How To Fix")
    finding_targets: dict[str, str] = {}
    for item in shortfall_items:
        target = _finding_target(item.text)
        if target is not None:
            finding_targets[item.identifier] = target
    return {
        "status": document.frontmatter["status"],
        "summary": _item_texts(document, "Summary")[0],
        "what_came_up_short": [item.text for item in shortfall_items],
        "finding_ids": [item.identifier for item in shortfall_items],
        "finding_targets": finding_targets,
        "how_to_fix": []
        if how_to_fix is None
        else [
            f"{item.identifier}: {item.text}"
            for item in how_to_fix.items
        ],
    }


def _normalize(content: dict[str, object]) -> dict[str, object]:
    return normalize_analysis_decision_content(content)


def _validate_decision_contract(document: ParsedDocument) -> list[Diagnostic]:
    artifact_type = document.frontmatter["type"]
    status = document.frontmatter["status"]
    what_section = document.section("What Came Up Short")
    fix_section = document.section("How To Fix")
    if status == "completed":
        summary_section = document.section("Summary")
        diagnostics = [
            Diagnostic(
                section.line,
                section.name,
                "ANALYSIS002",
                "status 'completed' must omit both remediation sections; known gaps require a non-completed status",
            )
            for section in (what_section, fix_section)
            if section is not None
        ]
        if artifact_type in _VERIFICATION_TYPES and summary_section is not None:
            diagnostics.extend(
                Diagnostic(
                    item.line,
                    "Summary",
                    "ANALYSIS006",
                    "completed verification decisions must cite evidence with 'Evidence:' rather than assert success",
                )
                for item in summary_section.items
                if _COMPLETED_EVIDENCE_MARKER not in item.text
            )
        return diagnostics
    if status not in {"request_changes", "failed"}:
        return []
    what_items = () if what_section is None else what_section.items
    summary_section = document.section("Summary")
    diagnostics = [
        Diagnostic(
            1 if summary_section is None else summary_section.line,
            "What Came Up Short",
            "ANALYSIS003",
            "non-completed decisions require a non-empty What Came Up Short section",
        )
    ] if not what_items else []
    if artifact_type in _VERIFICATION_TYPES:
        diagnostics.extend(
            Diagnostic(
                item.line,
                "What Came Up Short",
                "ANALYSIS005",
                "verification finding must include Criterion:, Expected observation:, Verdict:, Evidence:, and Location:",
            )
            for item in what_items
            if any(field not in item.text for field in _REQUIRED_FINDING_FIELDS)
        )
    diagnostics.extend(
        Diagnostic(
            item.line,
            "What Came Up Short",
            "ANALYSIS007",
            "a 'not evaluable' verification verdict requires status 'failed'",
        )
        for item in what_items
        if status != "failed" and _NOT_EVALUABLE_MARKER in item.text.casefold()
    )
    if artifact_type == "planning_analysis_decision":
        diagnostics.extend(
            Diagnostic(
                item.line,
                "What Came Up Short",
                "ANALYSIS004",
                "planning request_changes finding must identify its affected target as 'Step: [S-n]' or 'Plan-level:'",
            )
            for item in what_items
            if not _FINDING_TARGET_PATTERN.search(item.text)
        )
    if artifact_type != "review_analysis_decision":
        return diagnostics
    fix_items = () if fix_section is None else fix_section.items
    what_ids = {item.identifier for item in what_items}
    fix_ids = {item.identifier for item in fix_items}
    diagnostics.extend(
        Diagnostic(item.line, "What Came Up Short", "ANALYSIS003", "What Came Up Short item has no matching How To Fix item")
        for item in what_items
        if item.identifier not in fix_ids
    )
    diagnostics.extend(
        Diagnostic(item.line, "How To Fix", "ANALYSIS003", "How To Fix item has no matching What Came Up Short item")
        for item in fix_items
        if item.identifier not in what_ids
    )
    return diagnostics


def _spec(artifact_type: str) -> MdArtifactSpec:
    return MdArtifactSpec(
        artifact_type=artifact_type,
        required_frontmatter=frozenset({"type", "status"}),
        closed_frontmatter={
            "type": FrontmatterVocabulary((artifact_type,), "ANALYSIS001"),
            "status": FrontmatterVocabulary(_STATUSES),
        },
        sections={
            "Summary": SectionRule(require_items=True, max_items=1, allow_body=True),
            "What Came Up Short": SectionRule(required=False, allow_body=True),
            "How To Fix": SectionRule(required=False, allow_body=True),
        },
        to_content=_to_content,
        normalize_content=_normalize,
        validate_document=_validate_decision_contract,
        allow_unknown_frontmatter=True,
        allow_unknown_sections=True,
    )


ANALYSIS_DECISION_SPECS = tuple(_spec(artifact_type) for artifact_type in _ANALYSIS_TYPES)

for _specification in ANALYSIS_DECISION_SPECS:
    register_spec(_specification)


__all__ = ["ANALYSIS_DECISION_SPECS"]

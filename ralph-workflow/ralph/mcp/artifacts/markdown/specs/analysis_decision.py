"""Markdown specs for planning, development, and review analysis decisions.

Consumed structure (stays strict): frontmatter ``type`` and ``status``.
``status`` keeps its closed decision vocabulary (``completed`` |
``request_changes`` | ``failed`` — it routes the pipeline, so a wrong
status is a hard error naming the valid values), and ``How To Fix`` item
IDs feed downstream proof references. Section bodies are descriptive
and tolerate multi-line prose and unknown continuation lines.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING, cast

from ralph.mcp.artifacts.markdown import MdArtifactSpec, SectionRule
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._frontmatter_vocabulary import FrontmatterVocabulary
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.typed_artifacts import normalize_analysis_decision_content

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem

_ANALYSIS_TYPES = (
    "planning_analysis_decision",
    "development_analysis_decision",
    "review_analysis_decision",
    "policy_remediation_analysis_decision",
)
_STATUSES = ("completed", "request_changes", "failed")
_FINDING_TARGET_PATTERN = re.compile(r"(?:Step:\s*)?\[(S-[1-9][0-9]*)\]|Plan-level:", re.IGNORECASE)
_STEP_REFERENCE_PATTERN = re.compile(r"Step:\s*\[(S-[1-9][0-9]*)\]")


def _item_texts(document: ParsedDocument, section_name: str) -> list[str]:
    section = document.section(section_name)
    if section is None:
        return []
    return [item.text for item in cast("list[ParsedItem]", section.items)]


def _finding_target(text: str) -> str | None:
    if "Plan-level:" in text:
        return "plan-level"
    match = _STEP_REFERENCE_PATTERN.search(text)
    return None if match is None else match.group(1)


def _to_content(document: ParsedDocument) -> dict[str, object]:
    summary = _item_texts(document, "Summary")
    how_to_fix = document.section("How To Fix")
    shortfalls = _item_texts(document, "What Came Up Short")
    shortfall_section = document.section("What Came Up Short")
    finding_targets: dict[str, str] = {}
    if shortfall_section is not None:
        shortfall_items = cast("list[ParsedItem]", shortfall_section.items)
        for item in shortfall_items:
            target = _finding_target(item.text)
            if target is not None:
                finding_targets[item.identifier] = target
    step_references: list[str] = []
    for shortfall in shortfalls:
        match = _STEP_REFERENCE_PATTERN.search(shortfall)
        if match is not None:
            reference = match.group(1)
            if reference not in step_references:
                step_references.append(reference)
    status = document.frontmatter["status"]
    return {
        "status": status,
        "summary": summary[0],
        "what_came_up_short": shortfalls,
        "finding_targets": finding_targets,
        "step_references": step_references,
        # Keep the stable ID in the canonical string until proof consumers move
        # from legacy prose matching to the markdown ID contract.
        "how_to_fix": []
        if how_to_fix is None
        else [
            f"{item.identifier}: {item.text}"
            for item in cast("list[ParsedItem]", how_to_fix.items)
        ],
    }


def _normalize(content: dict[str, object]) -> dict[str, object]:
    return normalize_analysis_decision_content(content)


def _validate_decision_contract(document: ParsedDocument) -> list[Diagnostic]:
    status = document.frontmatter["status"]
    what_section = document.section("What Came Up Short")
    fix_section = document.section("How To Fix")

    if status == "completed":
        return [
            Diagnostic(
                section.line,
                section.name,
                "ANALYSIS002",
                "status 'completed' must omit both remediation sections; "
                "known gaps require a non-completed status",
            )
            for section in (what_section, fix_section)
            if section is not None
        ]

    if status not in {"request_changes", "failed"}:
        return []

    what_items = () if what_section is None else what_section.items
    fix_items = () if fix_section is None else fix_section.items
    what_ids = {item.identifier for item in what_items}
    fix_ids = {item.identifier for item in fix_items}
    diagnostics = [
        Diagnostic(
            item.line,
            "What Came Up Short",
            "ANALYSIS003",
            f"What Came Up Short item {item.identifier!r} has no matching "
            "How To Fix item; both remediation sections must use exactly "
            "the same IDs",
        )
        for item in what_items
        if item.identifier not in fix_ids
    ]
    diagnostics.extend(
        Diagnostic(
            item.line,
            "How To Fix",
            "ANALYSIS003",
            f"How To Fix item {item.identifier!r} has no matching What Came "
            "Up Short item; both remediation sections must use exactly the "
            "same IDs",
        )
        for item in fix_items
        if item.identifier not in what_ids
    )
    if status == "request_changes" and document.frontmatter["type"] == "planning_analysis_decision":
        diagnostics.extend(
            Diagnostic(
                item.line,
                "What Came Up Short",
                "ANALYSIS004",
                "planning request_changes finding must identify its affected target as "
                "'Step: [S-n]' or 'Plan-level:'",
            )
            for item in what_items
            if not _FINDING_TARGET_PATTERN.search(item.text)
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

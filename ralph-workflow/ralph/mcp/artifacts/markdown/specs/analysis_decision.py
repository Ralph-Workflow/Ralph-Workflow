"""Markdown specs for planning, development, and review analysis decisions."""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

from ralph.mcp.artifacts.markdown import LenientEnum, MdArtifactSpec, SectionRule
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.typed_artifacts import (
    _ANALYSIS_DECISION_VOCABULARY,
    normalize_analysis_decision_content,
)

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument

_ANALYSIS_TYPES = (
    "planning_analysis_decision",
    "development_analysis_decision",
    "review_analysis_decision",
    "policy_remediation_analysis_decision",
)


def _item_texts(document: ParsedDocument, section_name: str) -> list[str]:
    section = document.section(section_name)
    return [] if section is None else [item.text for item in section.items]


def _to_content(document: ParsedDocument) -> dict[str, object]:
    summary = _item_texts(document, "Summary")
    how_to_fix = document.section("How To Fix")
    return {
        "status": document.frontmatter["status"],
        "summary": summary[0],
        "what_came_up_short": _item_texts(document, "What Came Up Short"),
        # Keep the stable ID in the canonical string until proof consumers move
        # from legacy prose matching to the markdown ID contract.
        "how_to_fix": []
        if how_to_fix is None
        else [f"{item.identifier}: {item.text}" for item in how_to_fix.items],
    }


def _validate_type(expected_type: str) -> Callable[[ParsedDocument], list[Diagnostic]]:
    def validate(document: ParsedDocument) -> list[Diagnostic]:
        if document.frontmatter["type"] == expected_type:
            return []
        return [
            Diagnostic(
                document.frontmatter_lines["type"],
                None,
                "ANALYSIS001",
                f"frontmatter 'type' must be {expected_type!r}",
            )
        ]

    return validate


def _normalize(content: dict[str, object]) -> dict[str, object]:
    return normalize_analysis_decision_content(content)


def _spec(artifact_type: str) -> MdArtifactSpec:
    return MdArtifactSpec(
        artifact_type=artifact_type,
        required_frontmatter=frozenset({"type", "status"}),
        sections={
            "Summary": SectionRule(require_items=True, max_items=1),
            "What Came Up Short": SectionRule(required=False),
            "How To Fix": SectionRule(required=False),
        },
        to_content=_to_content,
        normalize_content=_normalize,
        lenient_enums={
            "status": LenientEnum(_ANALYSIS_DECISION_VOCABULARY, "completed"),
        },
        validate_document=_validate_type(artifact_type),
    )


ANALYSIS_DECISION_SPECS = tuple(_spec(artifact_type) for artifact_type in _ANALYSIS_TYPES)

for _specification in ANALYSIS_DECISION_SPECS:
    register_spec(_specification)


__all__ = ["ANALYSIS_DECISION_SPECS"]

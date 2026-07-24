"""Markdown spec for review issues.

Consumed structure (stays strict): frontmatter ``type`` and ``status``
(closed vocabulary ``issues_found`` | ``no_issues`` — review routing
reads it, so a wrong status is a hard error naming the valid values),
the required-section skeleton, and the ``path | severity | summary``
shape of ``Issues`` items. Per-issue severity is descriptive (no
consumer gates on it), so unknown severities warn-coerce to ``low``.
Section bodies tolerate multi-line prose and unknown continuation
lines under items.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown import MdArtifactSpec, SectionRule
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.typed_artifacts import normalize_issues_content

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem

_SEVERITIES = frozenset({"high", "medium", "low"})
_STATUSES = ("issues_found", "no_issues")
_ISSUE_PARTS = 3


def _item_texts(document: ParsedDocument, section_name: str) -> list[str]:
    section = document.section(section_name)
    return [] if section is None else [item.text for item in section.items]


def _issue_content(item: ParsedItem) -> dict[str, str]:
    parts = item.text.split(" | ", 2)
    if len(parts) != _ISSUE_PARTS:
        return {"path": "", "severity": "low", "summary": ""}
    path, severity, summary = parts
    return {
        "path": path,
        "severity": severity if severity in _SEVERITIES else "low",
        "summary": summary,
    }


def _to_content(document: ParsedDocument) -> dict[str, object]:
    issues = document.section("Issues")
    summary = _item_texts(document, "Summary")
    return {
        "status": document.frontmatter["status"],
        "summary": summary[0],
        "issues": [] if issues is None else [_issue_content(item) for item in issues.items],
        "what_came_up_short": _item_texts(document, "What Came Up Short"),
        "how_to_fix": _item_texts(document, "How To Fix"),
    }


def _validate_frontmatter(document: ParsedDocument) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if document.frontmatter["type"] != "issues":
        diagnostics.append(
            Diagnostic(
                document.frontmatter_lines["type"],
                None,
                "ISSUES001",
                "frontmatter 'type' must be 'issues'",
            )
        )
    if document.frontmatter["status"] not in _STATUSES:
        diagnostics.append(
            Diagnostic(
                document.frontmatter_lines["status"],
                None,
                "SPEC010",
                "frontmatter 'status' must be one of: issues_found, no_issues",
            )
        )
    return diagnostics


def _validate_document(document: ParsedDocument) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    issues = document.section("Issues")
    if issues is None:
        return diagnostics
    for item in issues.items:
        parts = item.text.split(" | ", 2)
        if len(parts) != _ISSUE_PARTS:
            diagnostics.append(
                Diagnostic(
                    item.line,
                    "Issues",
                    "ISSUES002",
                    "issue items must use 'path | severity | summary'",
                )
            )
        elif parts[1] not in _SEVERITIES:
            diagnostics.append(
                Diagnostic(
                    item.line,
                    "Issues",
                    "ISSUES003",
                    f"severity {parts[1]!r} coerced to 'low'",
                    "warning",
                )
            )
    return diagnostics


def _normalize(content: dict[str, object]) -> dict[str, object]:
    return normalize_issues_content(content)


ISSUES_SPEC = MdArtifactSpec(
    artifact_type="issues",
    required_frontmatter=frozenset({"type", "status"}),
    sections={
        "Summary": SectionRule(require_items=True, max_items=1, allow_body=True),
        "Issues": SectionRule(required=False, allow_body=True),
        "What Came Up Short": SectionRule(required=False, allow_body=True),
        "How To Fix": SectionRule(required=False, allow_body=True),
    },
    to_content=_to_content,
    normalize_content=_normalize,
    validate_frontmatter=_validate_frontmatter,
    validate_document=_validate_document,
)

register_spec(ISSUES_SPEC)

__all__ = ["ISSUES_SPEC"]

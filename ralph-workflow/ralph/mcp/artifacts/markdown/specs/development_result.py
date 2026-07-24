"""Markdown mapping and validation rules for ``development_result`` artifacts.

Consumed structure (stays strict): frontmatter ``status`` has the closed
vocabulary ``completed`` | ``partial`` (routing and continuation prompts
read it — a wrong status such as ``done`` is a hard error naming the
valid values), the required-section skeleton, and the ``Plan Items
Proven`` / ``Analysis Items Addressed`` item IDs (proof gating
cross-references them) plus the ``Continuation`` session ID. Everything
else is descriptive: sections tolerate multi-line prose and unknown
``Key: value`` continuation lines under items.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.development_result import (
    DEVELOPMENT_RESULT_ARTIFACT_TYPE,
    normalize_development_result_content,
)
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._section_rule import SectionRule
from ralph.mcp.artifacts.markdown._spec import Content, MdArtifactSpec

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument
from ralph.mcp.artifacts.markdown.registry import register_spec

_STATUSES = ("completed", "partial")


def _items(document: ParsedDocument, name: str) -> tuple[str, ...]:
    section = document.section(name)
    if section is None:
        raise ValueError(f"missing required section {name!r}")
    return tuple(item.text for item in section.items)


def _one_item(document: ParsedDocument, name: str) -> str:
    items = _items(document, name)
    if len(items) != 1:
        raise ValueError(f"{name} must contain exactly one item")
    return items[0]


def _proof_items(document: ParsedDocument, name: str, key: str) -> list[dict[str, str]]:
    section = document.section(name)
    if section is None:
        return []
    return [{key: item.identifier, "proof": item.text} for item in section.items]


def _to_content(document: ParsedDocument) -> Content:
    content: Content = {
        "status": document.frontmatter["status"],
        "summary": _one_item(document, "Summary"),
        "files_changed": "\n".join(_items(document, "Files Changed")),
        "plan_items_proven": _proof_items(document, "Plan Items Proven", "plan_item"),
        "analysis_items_addressed": _proof_items(
            document, "Analysis Items Addressed", "how_to_fix_item"
        ),
    }
    next_steps = document.section("Next Steps")
    if next_steps is not None:
        if len(next_steps.items) != 1:
            raise ValueError("Next Steps must contain exactly one item")
        content["next_steps"] = next_steps.items[0].text
    continuation = document.section("Continuation")
    if continuation is not None:
        if len(continuation.items) != 1:
            raise ValueError("Continuation must contain exactly one item")
        content["continuation"] = {"prior_session_id": continuation.items[0].text}
    return content


def _validate_frontmatter(document: ParsedDocument) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []
    if document.frontmatter["type"] != DEVELOPMENT_RESULT_ARTIFACT_TYPE:
        diagnostics.append(
            Diagnostic(
                document.frontmatter_lines["type"],
                None,
                "DEV002",
                f"frontmatter 'type' must be {DEVELOPMENT_RESULT_ARTIFACT_TYPE!r}",
            )
        )
    if document.frontmatter["status"] not in _STATUSES:
        diagnostics.append(
            Diagnostic(
                document.frontmatter_lines["status"],
                None,
                "SPEC010",
                "frontmatter 'status' must be one of: completed, partial",
            )
        )
    return diagnostics


DEVELOPMENT_RESULT_SPEC = MdArtifactSpec(
    artifact_type=DEVELOPMENT_RESULT_ARTIFACT_TYPE,
    required_frontmatter=frozenset({"type", "status"}),
    sections={
        "Summary": SectionRule(require_items=True, max_items=1, allow_body=True),
        "Files Changed": SectionRule(require_items=True, allow_body=True),
        "Plan Items Proven": SectionRule(required=False, allow_body=True),
        "Analysis Items Addressed": SectionRule(required=False, allow_body=True),
        "Next Steps": SectionRule(required=False, require_items=True, max_items=1, allow_body=True),
        "Continuation": SectionRule(required=False, require_items=True, max_items=1),
    },
    to_content=_to_content,
    normalize_content=normalize_development_result_content,
    validate_frontmatter=_validate_frontmatter,
)

register_spec(DEVELOPMENT_RESULT_SPEC)

__all__ = ["DEVELOPMENT_RESULT_SPEC"]

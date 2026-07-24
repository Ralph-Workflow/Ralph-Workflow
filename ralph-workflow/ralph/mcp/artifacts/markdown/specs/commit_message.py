"""Closed markdown specification for commit-message artifacts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.commit_message import normalize_commit_message_content
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._section_rule import SectionRule
from ralph.mcp.artifacts.markdown._spec import MdArtifactSpec
from ralph.mcp.artifacts.markdown.registry import register_spec

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection


def _to_content(document: ParsedDocument) -> dict[str, object]:
    """Map the closed commit/skip markdown variants to the existing payload shape."""
    kind = document.frontmatter["type"]
    if kind == "skip":
        return {"type": kind, "reason": document.frontmatter.get("reason", "")}
    content: dict[str, object] = {
        "type": kind,
        "subject": document.frontmatter.get("subject", ""),
    }
    content.update(_single_text_section(document.section("Body"), "body"))
    content.update(_single_text_section(document.section("Body Summary"), "body_summary"))
    content.update(_single_text_section(document.section("Body Details"), "body_details"))
    content.update(_single_text_section(document.section("Body Footer"), "body_footer"))
    content.update(_text_list_section(document.section("Files"), "files"))
    content.update(_excluded_files_section(document.section("Excluded Files")))
    return content


def _single_text_section(section: ParsedSection | None, field: str) -> dict[str, object]:
    if section is None:
        return {}
    return {field: section.items[0].text if section.items else ""}


def _text_list_section(section: ParsedSection | None, field: str) -> dict[str, object]:
    if section is None:
        return {}
    return {field: [item.text for item in section.items]}


def _excluded_files_section(section: ParsedSection | None) -> dict[str, object]:
    if section is None:
        return {}
    entries: list[dict[str, object]] = []
    for item in section.items:
        path, separator, reason = item.text.partition(" | ")
        if not separator or not path or not reason:
            raise ValueError("Excluded Files entries must use '<path> | <reason>'")
        entries.append({"path": path, "reason": reason})
    return {"excluded_files": entries}


def _validate_document(document: ParsedDocument) -> list[Diagnostic]:
    if document.frontmatter["type"] in {"commit", "skip"}:
        return []
    return [
        Diagnostic(
            document.frontmatter_lines["type"],
            None,
            "COMMIT001",
            "frontmatter 'type' must be 'commit' or 'skip'",
        )
    ]


COMMIT_MESSAGE_SPEC = MdArtifactSpec(
    artifact_type="commit_message",
    required_frontmatter=frozenset({"type"}),
    optional_frontmatter=frozenset({"subject", "reason"}),
    sections={
        "Body": SectionRule(required=False, max_items=1),
        "Body Summary": SectionRule(required=False, max_items=1),
        "Body Details": SectionRule(required=False, max_items=1),
        "Body Footer": SectionRule(required=False, max_items=1),
        "Files": SectionRule(required=False),
        "Excluded Files": SectionRule(required=False),
    },
    to_content=_to_content,
    normalize_content=normalize_commit_message_content,
    validate_frontmatter=_validate_document,
)

register_spec(COMMIT_MESSAGE_SPEC)

__all__ = ["COMMIT_MESSAGE_SPEC"]

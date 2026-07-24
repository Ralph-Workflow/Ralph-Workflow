"""Closed markdown specification for commit-cleanup artifacts."""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._section_rule import SectionRule
from ralph.mcp.artifacts.markdown._spec import MdArtifactSpec
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.typed_artifacts import normalize_commit_cleanup_content

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection


def _to_content(document: ParsedDocument) -> dict[str, object]:
    """Map the closed cleanup document grammar to the existing payload shape."""
    actions = document.section("Actions")
    assert actions is not None
    return {
        "analysis_complete": _parse_bool(document.frontmatter.get("analysis_complete", "")),
        "actions": [_parse_action(item.text) for item in actions.items],
        **_reason_content(document.section("Reason")),
    }


def _parse_bool(value: str) -> bool:
    if value == "true":
        return True
    if value == "false":
        return False
    raise ValueError("analysis_complete must be 'true' or 'false'")


def _parse_action(text: str) -> dict[str, object]:
    action, separator, value = text.partition(" | ")
    if not separator or not action or not value:
        raise ValueError("Actions entries must use '<action> | <path-or-pattern>'")
    if action == "delete_file":
        return {"action": action, "path": value}
    return {"action": action, "pattern": value}


def _reason_content(section: ParsedSection | None) -> dict[str, object]:
    if section is None:
        return {}
    return {"reason": section.items[0].text if section.items else ""}


def _validate_document(document: ParsedDocument) -> list[Diagnostic]:
    if document.frontmatter.get("type") in (None, "commit_cleanup"):
        return []
    return [
        Diagnostic(
            document.frontmatter_lines["type"],
            None,
            "CLEANUP001",
            "frontmatter 'type' must be 'commit_cleanup'",
        )
    ]


COMMIT_CLEANUP_SPEC = MdArtifactSpec(
    artifact_type="commit_cleanup",
    required_frontmatter=frozenset({"type", "analysis_complete"}),
    sections={
        "Reason": SectionRule(required=False, max_items=1),
        "Actions": SectionRule(required=True),
    },
    to_content=_to_content,
    normalize_content=normalize_commit_cleanup_content,
    validate_document=_validate_document,
)

register_spec(COMMIT_CLEANUP_SPEC)

__all__ = ["COMMIT_CLEANUP_SPEC"]

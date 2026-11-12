from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown._frontmatter_vocabulary import FrontmatterVocabulary
from ralph.mcp.artifacts.markdown._section_rule import SectionRule
from ralph.mcp.artifacts.markdown._spec import MdArtifactSpec
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.typed_artifacts import normalize_commit_review_content

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_section import ParsedSection

_TREE_SHA_PATTERN: re.Pattern[str] = re.compile(r"^[0-9a-fA-F]{40}$")


def _to_content(document: ParsedDocument) -> dict[str, object]:
    actions = document.section("Actions")
    action_items = actions.items if actions is not None else ()
    return {
        "verdict": document.frontmatter.get("verdict", ""),
        "tree_sha": document.frontmatter.get("tree_sha", ""),
        "actions": [_parse_action(item.text) for item in action_items],
        **_reason_content(document.section("Reason")),
    }


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
    items = [item.text for item in section.items]
    if not items:
        return {}
    return {"reason": "\n".join(items)}


def _validate_commit_review_document(document: ParsedDocument) -> list[Diagnostic]:
    diagnostics: list[Diagnostic] = []

    tree_sha = document.frontmatter.get("tree_sha")
    if tree_sha is not None and not _TREE_SHA_PATTERN.fullmatch(tree_sha):
        diagnostics.append(
            Diagnostic(
                document.frontmatter_lines.get("tree_sha", 1),
                None,
                "SPEC010",
                "frontmatter 'tree_sha' must be a 40-character hex string",
            )
        )

    verdict = document.frontmatter.get("verdict")
    if verdict == "reject":
        reason_section = document.section("Reason")
        if reason_section is None:
            diagnostics.append(
                Diagnostic(
                    1,
                    "Reason",
                    "SPEC008",
                    "missing required section 'Reason' when verdict is 'reject'",
                )
            )
        elif not reason_section.items:
            diagnostics.append(
                Diagnostic(
                    reason_section.line,
                    "Reason",
                    "SPEC006",
                    "section 'Reason' requires list items when verdict is 'reject'",
                )
            )

    return diagnostics


COMMIT_REVIEW_SPEC = MdArtifactSpec(
    artifact_type="commit_review",
    required_frontmatter=frozenset({"type", "verdict", "tree_sha"}),
    closed_frontmatter={
        "type": FrontmatterVocabulary(("commit_review",), "SPEC010"),
        "verdict": FrontmatterVocabulary(("approve", "reject"), "SPEC010"),
    },
    optional_frontmatter=frozenset({"reason"}),
    sections={
        "Reason": SectionRule(required=False),
        "Actions": SectionRule(required=True),
    },
    to_content=_to_content,
    normalize_content=normalize_commit_review_content,
    validate_document=_validate_commit_review_document,
    allow_unknown_frontmatter=False,
    allow_unknown_sections=True,
)

register_spec(COMMIT_REVIEW_SPEC)

__all__ = ["COMMIT_REVIEW_SPEC"]

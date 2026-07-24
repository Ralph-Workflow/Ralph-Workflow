"""Markdown spec for fix results.

Consumed structure (stays strict): frontmatter ``type`` and the
required-section skeleton. All section content is descriptive (no
pipeline consumer reads individual fields), so bodies tolerate
multi-line prose and unknown continuation lines under items.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.markdown import MdArtifactSpec, SectionRule
from ralph.mcp.artifacts.markdown._diagnostic import Diagnostic
from ralph.mcp.artifacts.markdown.registry import register_spec
from ralph.mcp.artifacts.typed_artifacts import normalize_fix_result_content

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument


def _item_texts(document: ParsedDocument, section_name: str) -> list[str]:
    section = document.section(section_name)
    return [] if section is None else [item.text for item in section.items]


def _to_content(document: ParsedDocument) -> dict[str, object]:
    summary = _item_texts(document, "Summary")
    next_steps = _item_texts(document, "Next Steps")
    return {
        "summary": summary[0],
        "files_changed": "\n".join(f"- {item}" for item in _item_texts(document, "Files Changed")),
        "next_steps": None if not next_steps else next_steps[0],
    }


def _validate_type(document: ParsedDocument) -> list[Diagnostic]:
    if document.frontmatter["type"] == "fix_result":
        return []
    return [
        Diagnostic(
            document.frontmatter_lines["type"],
            None,
            "FIX001",
            "frontmatter 'type' must be 'fix_result'",
        )
    ]


def _normalize(content: dict[str, object]) -> dict[str, object]:
    return normalize_fix_result_content(content)


FIX_RESULT_SPEC = MdArtifactSpec(
    artifact_type="fix_result",
    required_frontmatter=frozenset({"type"}),
    sections={
        "Summary": SectionRule(require_items=True, max_items=1, allow_body=True),
        "Files Changed": SectionRule(require_items=True, allow_body=True),
        "Next Steps": SectionRule(required=False, max_items=1, allow_body=True),
    },
    to_content=_to_content,
    normalize_content=_normalize,
    validate_frontmatter=_validate_type,
)

register_spec(FIX_RESULT_SPEC)

__all__ = ["FIX_RESULT_SPEC"]

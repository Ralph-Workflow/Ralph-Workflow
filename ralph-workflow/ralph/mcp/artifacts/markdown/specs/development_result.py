"""Markdown mapping and validation rules for ``development_result`` artifacts.

Frontmatter ``status`` always has the closed vocabulary ``completed`` |
``partial`` | ``failed`` (routing and continuation prompts read it — a wrong
status such as ``done`` is a hard error naming the valid values). Everything
below the frontmatter is free-form: the next agent reads it, never a
validator. Plan/analysis/visual-proof machinery and per-item ID matching,
dispositions, rationales, and the timebox-warned ``Incomplete Work`` CLOSED
grammar are all removed; coverage is judged by development analysis, not
checked mechanically here.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.development_result import (
    DEVELOPMENT_RESULT_ARTIFACT_TYPE,
    normalize_development_result_content,
)
from ralph.mcp.artifacts.markdown._frontmatter_vocabulary import FrontmatterVocabulary
from ralph.mcp.artifacts.markdown._spec import Content, MdArtifactSpec
from ralph.mcp.artifacts.markdown.registry import register_spec

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument

_STATUSES = ("completed", "partial", "failed")


def _clean_line_text(text: str) -> str:
    cleaned = text.strip()
    if cleaned.startswith("- "):
        cleaned = cleaned[2:].strip()
    return cleaned


def _to_content(document: ParsedDocument) -> Content:
    """Map any development_result body to a free-form content dict.

    The body below the frontmatter is the next agent's reading matter;
    status is the only field the validator gates. Legacy-shaped sections
    (Summary, Files Changed, Next Steps, Continuation) are extracted
    best-effort and on a strict opt-in basis: when present they are
    preserved so continuation prompts and history snapshots keep
    working, but no section is required, and a free-form prose body
    that omits every section is the default and accepted shape.
    """
    content: Content = {
        "status": document.frontmatter["status"],
        "summary": "",
        "files_changed": "",
    }
    summary_section = document.section("Summary")
    if summary_section is not None:
        if summary_section.items:
            content["summary"] = summary_section.items[0].text
        elif summary_section.lines:
            content["summary"] = "\n".join(
                _clean_line_text(line.text) for line in summary_section.lines if line.text.strip()
            )
    files_section = document.section("Files Changed")
    if files_section is not None:
        if files_section.items:
            content["files_changed"] = "\n".join(item.text for item in files_section.items)
        elif files_section.lines:
            content["files_changed"] = "\n".join(
                _clean_line_text(line.text) for line in files_section.lines if line.text.strip()
            )
    next_steps_section = document.section("Next Steps")
    if next_steps_section is not None:
        if next_steps_section.items:
            content["next_steps"] = "\n".join(item.text for item in next_steps_section.items)
        elif next_steps_section.lines:
            content["next_steps"] = "\n".join(
                _clean_line_text(line.text)
                for line in next_steps_section.lines
                if line.text.strip()
            )
    continuation_section = document.section("Continuation")
    if continuation_section is not None:
        prior_session_id = ""
        if continuation_section.items:
            prior_session_id = continuation_section.items[0].text.strip()
        elif continuation_section.lines:
            prior_session_id = "\n".join(
                _clean_line_text(line.text)
                for line in continuation_section.lines
                if line.text.strip()
            ).strip()
        if prior_session_id:
            content["continuation"] = {"prior_session_id": prior_session_id}
    return content


DEVELOPMENT_RESULT_SPEC = MdArtifactSpec(
    artifact_type=DEVELOPMENT_RESULT_ARTIFACT_TYPE,
    required_frontmatter=frozenset({"type", "status"}),
    closed_frontmatter={
        "type": FrontmatterVocabulary((DEVELOPMENT_RESULT_ARTIFACT_TYPE,), "DEV002"),
        "status": FrontmatterVocabulary(_STATUSES),
    },
    sections={},
    to_content=_to_content,
    normalize_content=normalize_development_result_content,
    allow_unknown_frontmatter=True,
    allow_unknown_sections=True,
    structured_body=lambda document: False,  # pragma: no cover - body is always free-form
)

register_spec(DEVELOPMENT_RESULT_SPEC)

__all__ = ["DEVELOPMENT_RESULT_SPEC"]

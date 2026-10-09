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


def _to_content(document: ParsedDocument) -> Content:
    """Map any development_result body to a free-form content dict.

    The body below the frontmatter is the next agent's reading matter;
    status is the only field the validator gates.
    """
    return {
        "status": document.frontmatter["status"],
        "summary": "",
        "files_changed": "",
        "plan_items_proven": [],
        "analysis_items_addressed": [],
    }


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

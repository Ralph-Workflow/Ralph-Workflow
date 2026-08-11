"""Markdown mapping and validation rules for ``development_result`` artifacts.

Frontmatter ``status`` always has the closed vocabulary ``completed`` |
``partial`` (routing and continuation prompts read it — a wrong status
such as ``done`` is a hard error naming the valid values). Everything
below the frontmatter is validated only for a ``completed`` result,
because only a completion claim is checkable: the required-section
skeleton, the ``Plan Items Proven`` / ``Analysis Items Addressed`` item
IDs (proof gating cross-references them) and the ``Continuation``
session ID. A non-``completed`` result is free-form prose — no section
is required or shaped, and it is mapped best-effort so the next
iteration can read whatever the agent managed to write.

Within a ``completed`` body the rest stays descriptive: sections
tolerate multi-line prose and unknown ``Key: value`` continuation lines
under items.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from ralph.mcp.artifacts.development_result import (
    DEVELOPMENT_RESULT_ARTIFACT_TYPE,
    normalize_development_result_content,
)
from ralph.mcp.artifacts.markdown._frontmatter_vocabulary import FrontmatterVocabulary
from ralph.mcp.artifacts.markdown._section_rule import SectionRule
from ralph.mcp.artifacts.markdown._spec import Content, MdArtifactSpec

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem
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


def _proof_items(document: ParsedDocument, name: str, key: str) -> list[dict[str, object]]:
    section = document.section(name)
    if section is None:
        return []
    proofs: list[dict[str, object]] = []
    for item in section.items:
        proof: dict[str, object] = {key: item.identifier, "proof": item.text}
        fields = {
            line.text.split(": ", 1)[0]: line.text.split(": ", 1)[1]
            for line in item.fields
            if ": " in line.text
        }
        verdict_id = fields.get("Verdict ID")
        if verdict_id:
            proof["verdict_id"] = verdict_id
        before_handles = tuple(
            handle.strip()
            for handle in fields.get("Before Captures", "").split(",")
            if handle.strip()
        )
        after_handles = tuple(
            handle.strip()
            for handle in fields.get("After Captures", "").split(",")
            if handle.strip()
        )
        if before_handles and after_handles:
            proof["capture_handles"] = before_handles + after_handles
        proofs.append(proof)
    return proofs


def _is_completed(document: ParsedDocument) -> bool:
    return document.frontmatter.get("status") == "completed"


def _first_item(document: ParsedDocument, name: str) -> str | None:
    """Read the first item of an optional section, tolerating any shape."""
    section = document.section(name)
    if section is None or not section.items:
        return None
    return section.items[0].text


def _free_form_content(document: ParsedDocument) -> Content:
    """Map a non-``completed`` result: status plus whatever prose exists.

    Nothing below the frontmatter is required or shaped, so every
    section is read best-effort and a missing one simply contributes
    nothing. Proof entries are carried only when they are well formed —
    proof gating is skipped for a non-completion claim, so a degenerate
    entry is dropped rather than rejected.
    """
    content: Content = {
        "status": document.frontmatter["status"],
        "summary": _first_item(document, "Summary") or "",
        "files_changed": "\n".join(
            item.text for item in _optional_items(document, "Files Changed") if item.text
        ),
        "plan_items_proven": _well_formed_proofs(document, "Plan Items Proven", "plan_item"),
        "analysis_items_addressed": _well_formed_proofs(
            document, "Analysis Items Addressed", "how_to_fix_item"
        ),
    }
    next_steps = _first_item(document, "Next Steps")
    if next_steps:
        content["next_steps"] = next_steps
    prior_session_id = _first_item(document, "Continuation")
    if prior_session_id:
        content["continuation"] = {"prior_session_id": prior_session_id}
    return content


def _optional_items(document: ParsedDocument, name: str) -> tuple[ParsedItem, ...]:
    section = document.section(name)
    return () if section is None else tuple(section.items)


def _well_formed_proofs(document: ParsedDocument, name: str, key: str) -> list[dict[str, str]]:
    return [
        {key: item.identifier, "proof": item.text}
        for item in _optional_items(document, name)
        if item.identifier and item.text
    ]


def _to_content(document: ParsedDocument) -> Content:
    if not _is_completed(document):
        return _free_form_content(document)
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


DEVELOPMENT_RESULT_SPEC = MdArtifactSpec(
    artifact_type=DEVELOPMENT_RESULT_ARTIFACT_TYPE,
    required_frontmatter=frozenset({"type", "status"}),
    closed_frontmatter={
        "type": FrontmatterVocabulary((DEVELOPMENT_RESULT_ARTIFACT_TYPE,), "DEV002"),
        "status": FrontmatterVocabulary(_STATUSES),
    },
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
    allow_unknown_frontmatter=True,
    allow_unknown_sections=True,
    structured_body=_is_completed,
)

register_spec(DEVELOPMENT_RESULT_SPEC)

__all__ = ["DEVELOPMENT_RESULT_SPEC"]

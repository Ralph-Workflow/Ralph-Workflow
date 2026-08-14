"""Markdown mapping and validation rules for ``development_result`` artifacts.

Frontmatter ``status`` always has the closed vocabulary ``completed`` |
``partial`` | ``failed`` (routing and continuation prompts read it — a wrong status
such as ``done`` is a hard error naming the valid values). Everything
below the frontmatter is validated only for a ``completed`` result,
because only a completion claim is checkable: the required-section
skeleton, the ``Plan Items Proven`` / ``Analysis Items Addressed`` item
IDs (proof gating cross-references them) and the ``Continuation``
session ID. A non-``completed`` result requires at minimum a ``Summary``
section (the concise reason for the outcome) so silent omission is
rejected mechanically; the rest of the body is mapped best-effort so
the next iteration can read whatever the agent managed to write.

Within a ``completed`` body the rest stays descriptive: sections
tolerate multi-line prose and unknown ``Key: value`` continuation lines
under items.
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.development_result import (
    DEVELOPMENT_RESULT_ARTIFACT_TYPE,
    normalize_development_result_content,
)
from ralph.mcp.artifacts.markdown._frontmatter_vocabulary import FrontmatterVocabulary
from ralph.mcp.artifacts.markdown._section_rule import SectionRule
from ralph.mcp.artifacts.markdown._spec import Content, MdArtifactSpec
from ralph.mcp.protocol.cycle_deadline_env import cycle_warning_is_active

if TYPE_CHECKING:
    from ralph.mcp.artifacts.markdown._document import ParsedDocument
    from ralph.mcp.artifacts.markdown._parsed_item import ParsedItem
from ralph.mcp.artifacts.markdown.registry import register_spec

_STATUSES = ("completed", "partial", "failed")


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
        disposition = fields.get("Disposition")
        if disposition:
            proof["disposition"] = disposition
        rationale = fields.get("Rationale")
        if rationale:
            proof["rationale"] = rationale
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

    A ``Summary`` section is required so the operator and the next
    iteration always have a concise reason for the partial/failed
    outcome — silent omission is rejected mechanically rather than
    relying on prompt prose alone. Everything else is read best-effort:
    missing sections contribute nothing, and proof entries are carried
    only when well formed (proof gating is skipped for a non-completion
    claim, so a degenerate entry is dropped rather than rejected).

    When ``cycle_timebox_warned: true`` is set in the frontmatter, an
    ``## Incomplete Work`` section with at least one stable-ID item is
    required so the operator can trace what was interrupted by the
    deadline without re-reading the entire transcript. Each item must
    include a concise ``Reason:`` field and a supporting ``Evidence:``
    field with a reproducible location; items without these are rejected
    so fabricated completion or silent omission cannot pass validation.
    """
    summary = _first_item(document, "Summary")
    if not summary:
        raise ValueError(
            "Summary section with at least one item is required for "
            "partial/failed development results"
        )
    content: Content = {
        "status": document.frontmatter["status"],
        "summary": _first_item(document, "Summary") or "",
        "files_changed": "\n".join(
            item.text for item in _optional_items(document, "Files Changed") if item.text
        ),
        "plan_items_proven": [],
        "analysis_items_addressed": [],
    }
    # When the cycle timebox fired a warning before the partial/failed
    # outcome, require an Incomplete Work section listing what remains.
    if _cycle_timebox_warned(document):
        incomplete_items = _optional_items(document, "Incomplete Work")
        if not incomplete_items:
            raise ValueError(
                "Incomplete Work section with at least one stable-ID item "
                "is required when cycle_timebox_warned is set; each item "
                "must include a 'Reason:' and 'Evidence:' field"
            )
        _validate_warned_incomplete_items(incomplete_items)
        content["incomplete_work"] = [
            f"[{item.identifier}] {item.text}" for item in incomplete_items
        ]
    else:
        existing = _optional_items(document, "Incomplete Work")
        if existing:
            content["incomplete_work"] = [item.text for item in existing]
    next_steps = _first_item(document, "Next Steps")
    if next_steps:
        content["next_steps"] = next_steps
    prior_session_id = _first_item(document, "Continuation")
    if prior_session_id:
        content["continuation"] = {"prior_session_id": prior_session_id}
    return content


def _cycle_timebox_warned(document: ParsedDocument) -> bool:
    """Return whether this result was produced after a cycle-timebox warning.

    The runtime's published deadline is consulted first and is sufficient on
    its own: keying the gate solely on the reporter's own frontmatter made it
    self-defeating, since an agent tempted to hide unfinished work is exactly
    the agent that would omit — or misspell, given unknown frontmatter keys are
    tolerated — the flag that triggers the check. The declared flag is still
    honoured so a result validated outside the warned invocation (a replay, a
    hand-written report) keeps its stricter reading.
    """
    if cycle_warning_is_active(now_epoch=time.time()):
        return True
    declared = document.frontmatter.get("cycle_timebox_warned")
    return declared is not None and declared.lower() in ("true", "1", "yes")


def _optional_items(document: ParsedDocument, name: str) -> tuple[ParsedItem, ...]:
    section = document.section(name)
    return () if section is None else tuple(section.items)


def _item_fields(item: ParsedItem) -> dict[str, str]:
    """Extract ``Key: value`` pairs from an item's indented continuation lines."""
    return {
        line.text.split(": ", 1)[0]: line.text.split(": ", 1)[1]
        for line in item.fields
        if ": " in line.text
    }


def _validate_warned_incomplete_items(items: tuple[ParsedItem, ...]) -> None:
    """Mechanically require a stable ID, evidence, and reason for warned items.

    When the cycle timebox fired a soft warning, each ``## Incomplete Work``
    item must carry a stable-ID bracket, a concise ``Reason:`` field, and a
    supporting ``Evidence:`` field with a reproducible location — so the
    operator and next iteration can triage what was interrupted without
    re-reading the transcript or accepting fabricated completion.
    """
    for item in items:
        if not item.identifier:
            raise ValueError(
                f"Incomplete Work items must include a stable-ID bracket "
                f"(line {item.line})"
            )
        fields = _item_fields(item)
        reason = fields.get("Reason", "").strip()
        if not reason:
            raise ValueError(
                f"Incomplete Work item [{item.identifier}] must include a "
                f"concise 'Reason:' field explaining why the step is "
                f"incomplete or infeasible (line {item.line})"
            )
        evidence = fields.get("Evidence", "").strip()
        if not evidence:
            raise ValueError(
                f"Incomplete Work item [{item.identifier}] must include a "
                f"supporting 'Evidence:' field with a reproducible location "
                f"(line {item.line})"
            )


def _to_content(document: ParsedDocument) -> Content:
    if not _is_completed(document):
        return _free_form_content(document)
    if _cycle_timebox_warned(document) and not _optional_items(document, "Plan Items Proven"):
        # Gating only the partial/failed branch left the honesty requirement
        # keyed on the single word the reporting agent chooses: under a live
        # deadline warning the honest partial was rejected while a bare
        # `completed` — no proof section at all — was accepted. A completion
        # claim made under warning has to name what it proved.
        raise ValueError(
            "Plan Items Proven with at least one item is required for a "
            "'completed' development result submitted after the cycle timebox "
            "warning; report unfinished work as 'partial' or 'failed' with an "
            "Incomplete Work section instead of claiming completion"
        )
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
        "Incomplete Work": SectionRule(required=False, require_items=True, allow_body=True),
    },
    to_content=_to_content,
    normalize_content=normalize_development_result_content,
    allow_unknown_frontmatter=True,
    allow_unknown_sections=True,
    structured_body=_is_completed,
)

register_spec(DEVELOPMENT_RESULT_SPEC)

__all__ = ["DEVELOPMENT_RESULT_SPEC"]

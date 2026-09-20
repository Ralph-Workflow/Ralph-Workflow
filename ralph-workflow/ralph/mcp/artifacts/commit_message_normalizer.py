"""Conservative normalization for repairable commit-message drafts."""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.commit_message_ir import (
    build_commit_message_ir,
    render_commit_message_artifact,
)

if TYPE_CHECKING:
    from ralph.prompts.commit_evidence import CommitEvidenceBundle

_FRONTMATTER_SUBJECT = re.compile(r"(?im)^subject:\s*(.+)$")
_SUBJECT = re.compile(r"(?m)^([a-z]+(?:\([^)]+\))?!?:\s*.+)$")
_BODY_SECTION = re.compile(r"(?ims)^## Body\s*\n(.*?)(?=^## |\Z)")
_BODY_ITEM = re.compile(r"(?m)^\s*-\s+(?:\[[A-Z]+-\d+\]\s*)?(.*\S)\s*$")


@dataclass(frozen=True)
class NormalizationResult:
    """Normalized artifact content plus the deterministic repair audit."""

    content: str
    transformations: tuple[str, ...]
    confidence: str


def normalize_commit_message_draft(content: str, evidence: CommitEvidenceBundle) -> NormalizationResult:
    """Repair syntax while retaining explicit body claims and live file facts."""
    if re.search(r"(?m)^type:\s*skip\s*$", content):
        return NormalizationResult(content, (), "high")
    match = _FRONTMATTER_SUBJECT.search(content) or _SUBJECT.search(content)
    if match is None:
        raise ValueError(
            "commit evidence regeneration required: intent is ambiguous; expected "
            "'<kind>(<scope>)?: <lowercase description>', actual draft has no conventional subject"
        )
    subject = _normalized_subject(_capture_subject(match))
    if not subject:
        raise ValueError(
            "commit evidence regeneration required: intent is ambiguous; expected "
            "'<kind>(<scope>)?: <lowercase description>', actual subject is invalid"
        )
    ir = build_commit_message_ir(evidence, subject=subject)
    body = _extract_body(content)
    if body:
        ir = replace(ir, rationale=body)
    rendered = render_commit_message_artifact(ir)
    transformations: list[str] = []
    if rendered != content:
        transformations.append("rendered canonical artifact from live evidence")
    return NormalizationResult(rendered, tuple(transformations), "high")


def _extract_body(content: str) -> tuple[str, ...]:
    """Keep explicit body items; unstructured prose is not a grounded claim."""
    section = _BODY_SECTION.search(content)
    if section is None:
        return ()
    return tuple(match.group(1) for match in _BODY_ITEM.finditer(section.group(1)))


def _capture_subject(match: re.Match[str]) -> str:
    value = match.group(1)
    return value.strip() if isinstance(value, str) else ""


def _normalized_subject(subject: str) -> str:
    prefix, separator, description = subject.partition(":")
    if not separator:
        return ""
    description = description.strip()
    if not description:
        return ""
    return f"{prefix.lower()}: {description[:1].lower()}{description[1:]}"


__all__ = ["NormalizationResult", "normalize_commit_message_draft"]

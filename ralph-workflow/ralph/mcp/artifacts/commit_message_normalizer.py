"""Conservative normalization for repairable commit-message drafts."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING

from ralph.mcp.artifacts.commit_message_ir import (
    build_commit_message_ir,
    render_commit_message_artifact,
)

if TYPE_CHECKING:
    from ralph.prompts.commit_evidence import CommitEvidenceBundle

_FRONTMATTER_SUBJECT = re.compile(r"(?im)^subject:\s*(.+)$")
_SUBJECT = re.compile(r"(?m)^([a-z]+(?:\([^)]+\))?!?:\s*.+)$")


@dataclass(frozen=True)
class NormalizationResult:
    """Normalized artifact content plus the deterministic repair audit."""

    content: str
    transformations: tuple[str, ...]
    confidence: str


def normalize_commit_message_draft(content: str, evidence: CommitEvidenceBundle) -> NormalizationResult:
    """Repair only deterministic syntax and replace file selection with live facts."""
    if re.search(r"(?m)^type:\s*skip\s*$", content):
        return NormalizationResult(content, (), "high")
    match = _FRONTMATTER_SUBJECT.search(content)
    if match is None:
        match = _SUBJECT.search(content)
    if match is None:
        raise ValueError("commit intent is ambiguous: provide a conventional commit subject")
    subject = _normalized_subject(_capture_subject(match))
    if not subject:
        raise ValueError("commit intent is ambiguous: provide a conventional commit subject")
    ir = build_commit_message_ir(evidence, subject=subject)
    rendered = render_commit_message_artifact(ir)
    transformations: list[str] = []
    if rendered != content:
        transformations.append("rendered canonical artifact from live evidence")
    return NormalizationResult(rendered, tuple(transformations), "high")


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

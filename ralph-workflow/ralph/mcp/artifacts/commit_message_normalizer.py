"""Conservative, evidence-grounded normalization for commit-message drafts."""

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
_SUBJECT = re.compile(r"(?m)^\s*([a-z]+(?:\([^)]+\))?!?:\s*.+)$")
_BODY_SECTION = re.compile(r"(?ims)^## (?:Body|Rationale|Changes?)\s*\n(.*?)(?=^## |\Z)")
_BODY_ITEM = re.compile(r"(?m)^\s*(?:[-*]|\d+\.)\s+(?:\[[A-Z]+-\d+\]\s*)?(.*\S)\s*$")
_MIN_CLAIM_TOKEN_LENGTH = 4


@dataclass(frozen=True)
class NormalizationResult:
    """Normalized content plus a deterministic, auditable repair record."""

    content: str
    transformations: tuple[str, ...]
    confidence: str
    provenance: tuple[str, ...] = ()


def normalize_commit_message_draft(content: str, evidence: CommitEvidenceBundle) -> NormalizationResult:
    """Repair structure from live facts; reject prose claims that cannot be grounded."""
    if re.search(r"(?m)^type:\s*skip\s*$", content):
        return NormalizationResult(content, (), "high", ("draft",))
    match = _FRONTMATTER_SUBJECT.search(content) or _SUBJECT.search(content)
    if match is None:
        raise ValueError("commit evidence regeneration required: intent is ambiguous; expected '<kind>(<scope>)?: <lowercase description>', actual draft has no conventional subject")
    subject = _normalized_subject(_capture_subject(match))
    if not subject:
        raise ValueError("commit evidence regeneration required: intent is ambiguous; expected '<kind>(<scope>)?: <lowercase description>', actual subject is invalid")
    ir = build_commit_message_ir(evidence, subject=subject)
    claims = _extract_claims(content)
    grounded = _grounded_claims(claims, evidence)
    if claims and not grounded:
        unsupported = claims[0]
        raise ValueError(f"commit evidence regeneration required: unsupported body claim {unsupported!r}; expected a claim grounded in live diff or durable evidence")
    if grounded:
        ir = replace(ir, rationale=grounded)
    rendered = render_commit_message_artifact(ir)
    if rendered == content:
        return NormalizationResult(rendered, (), "high", ("live evidence",))
    transformations = ["rendered canonical artifact from live evidence"]
    if claims:
        transformations.append("preserved grounded draft claims")
    if "## Files" in content:
        transformations.append("refreshed file selection from live changed set")
    return NormalizationResult(rendered, tuple(transformations), "high", ("live evidence", "draft" if claims else ""))


def _extract_claims(content: str) -> tuple[str, ...]:
    section = _BODY_SECTION.search(content)
    if section is None:
        return ()
    return tuple(match.group(1) for match in _BODY_ITEM.finditer(section.group(1)))


def _grounded_claims(claims: tuple[str, ...], evidence: CommitEvidenceBundle) -> tuple[str, ...]:
    facts = (*evidence.behavior_facts, *evidence.verification_facts, *evidence.compatibility_hints, *evidence.risk_hints, *evidence.diff_summary)
    canonical = tuple(f"Changed {area}." for area in evidence.change_areas)
    if not facts:
        return tuple(claim for claim in claims if claim in canonical)
    return tuple(claim for claim in claims if claim in canonical or any(_claim_matches(claim, fact) for fact in facts))


def _claim_matches(claim: str, fact: str) -> bool:
    words = _claim_words(claim)
    evidence_words = _claim_words(fact)
    return bool(words and words <= evidence_words)


def _claim_words(text: str) -> set[str]:
    """Return normalized meaningful tokens without regex Any leakage."""
    return {token.lower() for token in text.replace(".", " ").replace(",", " ").split() if len(token) >= _MIN_CLAIM_TOKEN_LENGTH}


def _capture_subject(match: re.Match[str]) -> str:
    value = match.group(1)
    return value.strip() if isinstance(value, str) else ""


def _normalized_subject(subject: str) -> str:
    prefix, separator, description = subject.partition(":")
    description = description.strip()
    if not separator or not description:
        return ""
    return f"{prefix.lower()}: {description[:1].lower()}{description[1:]}"


__all__ = ["NormalizationResult", "normalize_commit_message_draft"]

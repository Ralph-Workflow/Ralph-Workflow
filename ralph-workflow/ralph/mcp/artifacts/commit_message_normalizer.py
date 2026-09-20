"""Permissive, evidence-aware normalization for commit-message drafts.

Body prose is accepted by default. Token overlap with precomputed evidence facts
is used only to score confidence, never as a hard rejection gate. Regeneration
is reserved for ambiguous commit subjects (and other safety/executability
failures outside this module).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from ralph.mcp.artifacts._normalization_types import Confidence, NormalizationTransformation
from ralph.mcp.artifacts.commit_message_ir import (
    build_commit_message_ir,
    render_commit_message_artifact,
)

if TYPE_CHECKING:
    from ralph.prompts.commit_evidence import CommitEvidenceBundle

_FRONTMATTER_SUBJECT: re.Pattern[str] = re.compile(r"(?im)^subject:\s*(.+)$")
_SUBJECT: re.Pattern[str] = re.compile(r"(?im)^\s*((?:[a-z]+(?:\([^)]+\))?!?)(?::\s*|\s+).+)$")
_HEADING: re.Pattern[str] = re.compile(r"(?m)^##+\s+.*\S\s*$")
_BODY_ITEM: re.Pattern[str] = re.compile(r"(?m)^\s*(?:[-*]|\d+\.)\s+(?:\[[A-Z]+-\d+\]\s*)?(.*\S)\s*$")
_KEY_VALUE: re.Pattern[str] = re.compile(r"(?im)^\s*(?:intent|rationale|changes?|verification)\s*:\s*(.+\S)\s*$")
_MIN_CLAIM_TOKEN_LENGTH = 4
_SUBJECT_PART_COUNT = 2


@dataclass(frozen=True)
class NormalizationResult:
    """Normalized content plus a deterministic, auditable repair record."""

    content: str
    transformations: tuple[NormalizationTransformation, ...]
    confidence: Confidence
    provenance: tuple[str, ...] = ()


def normalize_commit_message_draft(
    content: str,
    evidence: CommitEvidenceBundle,
    *,
    draft_revision: int = 0,
) -> NormalizationResult:
    """Repair recognizable drafts from live facts; keep relevant body prose."""
    if re.search(r"(?m)^type:\s*skip\s*$", content):
        return NormalizationResult(content, (), "high", ("draft",))
    match = _FRONTMATTER_SUBJECT.search(content) or _SUBJECT.search(content)
    if match is None:
        raise ValueError(_regeneration_diagnostic(
            "intent is ambiguous",
            "a conventional subject", "draft has no conventional subject", evidence,
            draft_revision, "conventional-subject repair and live-file refresh",
        ))
    subject = _normalized_subject(_capture_subject(match))
    if not subject:
        raise ValueError(_regeneration_diagnostic(
            "intent is ambiguous",
            "'<kind>(<scope>)?: <lowercase description>'", _capture_subject(match), evidence,
            draft_revision, "conventional-subject repair and live-file refresh",
        ))
    ir = build_commit_message_ir(evidence, subject=subject)
    claims = _extract_claims(content)
    grounded, claim_transformations = _reconcile_claims(claims, evidence)
    if grounded:
        known_claims = (*ir.rationale, *ir.behavior_risk, *ir.verification)
        additional_claims = tuple(claim for claim in grounded if claim not in known_claims)
        if additional_claims:
            ir = replace(ir, rationale=(*ir.rationale, *additional_claims))
    rendered = render_commit_message_artifact(ir)
    if rendered == content:
        return NormalizationResult(rendered, (), "high", ("live evidence",))
    transformations: list[NormalizationTransformation] = [
        NormalizationTransformation("rendered canonical artifact from live evidence", "live evidence", "high"),
        *claim_transformations,
    ]
    canonical_claims = (*ir.rationale, *ir.behavior_risk, *ir.verification)
    if len(tuple(_BODY_ITEM.finditer(content))) > evidence.message_budget.max_body_points:
        transformations.append(NormalizationTransformation(
            "compressed duplicate or incidental body claims to evidence budget", "draft", "high"
        ))
    elif canonical_claims and not all(claim in claims for claim in canonical_claims):
        transformations.append(NormalizationTransformation(
            "expanded body with grounded evidence facts", "live evidence", "high"
        ))
    if "## Files" in content:
        transformations.append(NormalizationTransformation("refreshed file selection from live changed set", "changed files", "high"))
    confidence: Confidence = "medium" if any(item.confidence == "medium" for item in transformations) else "high"
    return NormalizationResult(rendered, tuple(transformations), confidence, ("live evidence", "draft" if claims else ""))


def _extract_claims(content: str) -> tuple[str, ...]:
    """Extract candidates from canonical and tolerant prose/list/key-value drafts."""
    candidates: list[str] = []
    in_files = False
    for line in content.splitlines():
        stripped = line.strip()
        if stripped.startswith("##"):
            in_files = stripped.lower() in {"## files", "## excluded files"}
            continue
        if not in_files and (claim := _list_claim(stripped)) and not claim.endswith(".py"):
            candidates.extend(part.strip() for part in claim.split("; ") if part.strip())
        if ":" in stripped:
            key, value = stripped.split(":", 1)
            if key.lower() in {"intent", "rationale", "change", "changes", "verification"} and value.strip():
                candidates.append(value.strip())
    if not candidates:
        candidates.extend(_sentence_claims(_prose_body(content)))
    unique: list[str] = []
    for candidate in candidates:
        if candidate not in unique:
            unique.append(candidate)
    return tuple(unique)


def _list_claim(line: str) -> str:
    match = _BODY_ITEM.match(line)
    if match is None:
        return ""
    value = match.group(1)
    return value.strip() if isinstance(value, str) else ""


def _prose_body(content: str) -> str:
    lines: list[str] = []
    in_frontmatter = False
    for line in content.splitlines():
        if line.strip() == "---":
            in_frontmatter = not in_frontmatter
        elif not in_frontmatter and not line.lower().startswith("subject:") and not _HEADING.match(line) and not _BODY_ITEM.match(line) and not _SUBJECT.match(line) and not re.match(r"^\s*-\s+\[F-\d+\]", line):
            lines.append(line.strip())
    return " ".join(line for line in lines if line)


def _sentence_claims(body: str) -> tuple[str, ...]:
    sentences: list[str] = []
    for sentence in body.replace("!", ".").replace("?", ".").split("."):
        stripped = sentence.strip()
        if stripped:
            sentences.append(f"{stripped}.")
    return tuple(sentences)


def _reconcile_claims(
    claims: tuple[str, ...], evidence: CommitEvidenceBundle
) -> tuple[tuple[str, ...], tuple[NormalizationTransformation, ...]]:
    """Accept body claims; score confidence from overlap without hard rejection.

    Missing evidence, weak lexical overlap, architectural paraphrases, and
    different phrasing are not conflicts. Prefer live facts only for confidence
    scoring and for completing the rendered IR from the evidence bundle.
    """
    facts = _facts(evidence)
    canonical = tuple(f"Changed {area}." for area in evidence.change_areas)
    grounded: list[str] = []
    transformations: list[NormalizationTransformation] = []
    for claim in claims:
        if claim in canonical or any(_claim_matches(claim, fact) == "high" for fact in facts):
            grounded.append(claim)
        elif any(_claim_matches(claim, fact) == "medium" for fact in facts):
            grounded.append(claim)
            transformations.append(NormalizationTransformation(
                "preserved partial-overlap draft claim", "live evidence", "medium"
            ))
        else:
            grounded.append(claim)
            transformations.append(NormalizationTransformation(
                "preserved relevant draft claim without exact evidence match", "draft", "medium"
            ))
    return tuple(grounded), tuple(transformations)


def _facts(evidence: CommitEvidenceBundle) -> tuple[str, ...]:
    return (*evidence.behavior_facts, *evidence.verification_facts, *evidence.compatibility_hints, *evidence.risk_hints, *evidence.diff_summary)


def _claim_matches(claim: str, fact: str) -> Confidence | None:
    words = _claim_words(claim)
    evidence_words = _claim_words(fact)
    if not words or not evidence_words:
        return None
    if words <= evidence_words:
        return "high"
    return "medium" if len(words & evidence_words) * 2 >= len(words) else None


def _claim_words(text: str) -> set[str]:
    """Return normalized meaningful tokens without regex Any leakage."""
    normalized = "".join(character if character.isalnum() or character in "_/-" else " " for character in text)
    return {token.lower() for token in normalized.split() if len(token) >= _MIN_CLAIM_TOKEN_LENGTH}


def _regeneration_diagnostic(reason: str, expected: str, actual: str, evidence: CommitEvidenceBundle, revision: int, attempted: str) -> str:
    fact_groups: tuple[tuple[str, tuple[str, ...]], ...] = (("behavior", evidence.behavior_facts), ("verification", evidence.verification_facts), ("compatibility", evidence.compatibility_hints), ("risk", evidence.risk_hints), ("diff", evidence.diff_summary))
    kinds = [name for name, facts in fact_groups if facts]
    summary = f"changed-file count: {len(evidence.changed_files)}; change areas: {', '.join(evidence.change_areas) or 'none'}; available fact kinds: {', '.join(kinds) or 'none'}"
    example = "fix: update changed files\n\n## Body\n- [B-1] Changed " + (evidence.change_areas[0] if evidence.change_areas else "code") + "."
    return (f"commit evidence regeneration required: {reason}; expected shape: {expected}; actual value/claim: {actual!r}; "
            f"evidence considered: {summary}; attempted normalization: {attempted}; draft revision: {revision}; minimal valid repair: {example!r}")


def _capture_subject(match: re.Match[str]) -> str:
    value = match.group(1)
    return value.strip() if isinstance(value, str) else ""


def _normalized_subject(subject: str) -> str:
    prefix, separator, description = subject.partition(":")
    if not separator:
        parts = subject.split(maxsplit=1)
        if len(parts) != _SUBJECT_PART_COUNT or parts[0].lower() not in {"build", "chore", "ci", "docs", "feat", "fix", "perf", "refactor", "revert", "style", "test"}:
            return ""
        prefix, description = parts
    description = description.strip()
    if not description:
        return ""
    return f"{prefix.lower()}: {description[:1].lower()}{description[1:]}"


__all__ = ["NormalizationResult", "normalize_commit_message_draft"]

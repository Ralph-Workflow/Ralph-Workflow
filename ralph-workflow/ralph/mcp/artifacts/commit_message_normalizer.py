"""Permissive, evidence-aware normalization for commit-message drafts.

Valid artifacts pass through byte-for-byte. Repair is limited to structural
syntax, and authored prose is preserved without lexical scoring.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, replace
from importlib import import_module
from typing import TYPE_CHECKING

from ralph.mcp.artifacts._normalization_types import Confidence, NormalizationTransformation
from ralph.mcp.artifacts.commit_message_ir import (
    build_commit_message_ir,
    render_commit_message_artifact,
)
from ralph.mcp.artifacts.markdown import parse_and_validate
from ralph.mcp.artifacts.markdown.registry import get_spec

if TYPE_CHECKING:
    from ralph.prompts.commit_evidence import CommitEvidenceBundle

_FRONTMATTER_SUBJECT: re.Pattern[str] = re.compile(r"(?im)^subject:\s*(.+)$")
_SUBJECT: re.Pattern[str] = re.compile(
    r"(?im)^\s*((?:(?:build|chore|ci|docs|feat|fix|perf|refactor|revert|style|test)"
    r"(?:\([^)]+\))?!?)(?::\s*|\s+).+)$"
)
_HEADING: re.Pattern[str] = re.compile(r"(?m)^##+\s+.*\S\s*$")
_BODY_ITEM: re.Pattern[str] = re.compile(r"(?m)^\s*(?:[-*]|\d+\.)\s+(?:\[[A-Z]+-\d+\]\s*)?(.*\S)\s*$")
_KEY_VALUE: re.Pattern[str] = re.compile(r"(?im)^\s*(?:intent|rationale|changes?|verification)\s*:\s*(.+\S)\s*$")
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
    if _is_valid_safe_artifact(content):
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
    ir = replace(ir, rationale=claims, behavior_risk=(), verification=(), files=())
    rendered = render_commit_message_artifact(ir)
    if rendered == content:
        return NormalizationResult(rendered, (), "high", ("live evidence",))
    transformations: list[NormalizationTransformation] = [
        NormalizationTransformation("rendered canonical artifact from live evidence", "live evidence", "high"),
    ]
    if "## Files" in content:
        transformations.append(NormalizationTransformation("removed file inventory", "draft", "high"))
    confidence: Confidence = "medium" if any(item.confidence == "medium" for item in transformations) else "high"
    return NormalizationResult(rendered, tuple(transformations), confidence, ("live evidence", "draft" if claims else ""))


def _is_valid_safe_artifact(content: str) -> bool:
    import_module("ralph.mcp.artifacts.markdown.specs")
    parsed, diagnostics = parse_and_validate(content, get_spec("commit_message"))
    if any(item.severity == "error" for item in diagnostics):
        return False
    files = parsed.get("files")
    return files is None


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
        if claim := _key_value_claim(stripped):
            candidates.append(claim)
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


def _key_value_claim(line: str) -> str:
    match = _KEY_VALUE.match(line)
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
        elif not in_frontmatter and not line.lower().startswith("subject:") and not _HEADING.match(line) and not _BODY_ITEM.match(line) and not _SUBJECT.match(line) and not _KEY_VALUE.match(line) and not re.match(r"^\s*-\s+\[F-\d+\]", line):
            lines.append(line.strip())
    return " ".join(line for line in lines if line)


def _sentence_claims(body: str) -> tuple[str, ...]:
    sentences: list[str] = []
    for sentence in body.replace("!", ".").replace("?", ".").split("."):
        stripped = sentence.strip()
        if stripped:
            sentences.append(f"{stripped}.")
    return tuple(sentences)


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
    subject = subject.strip().strip('"\'').strip()
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

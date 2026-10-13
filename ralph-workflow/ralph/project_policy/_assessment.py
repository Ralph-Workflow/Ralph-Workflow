"""Strict execution-evidence ledger parser for the readiness assessment.

The validator can only check FORM. The analysis agent can only check what
the LLM chose to write about. Neither proves a gate was actually run.

This module closes that gap with a strict, fail-closed ingestion contract:

* The :class:`ralph.project_policy._command_outcome.CommandOutcome` data
  class (defined in its own module per the class-per-module policy)
  records ONE gate's actual run: the command, its exit code, how long
  it took, and a SHA-256 of the policy content the agent claims to have
  validated.
* The :class:`ralph.project_policy._readiness_assessment.ReadinessAssessment`
  (also in its own module) exposes three default-false dimensions that
  downstream consumers surface alongside the existing status.
* :func:`parse_execution_evidence` is the only ingestion surface. It
  parses a list of ``RALPH-EVIDENCE:`` JSON lines (the exact convention
  the analysis agent is required to emit) into a strict, ordered tuple
  of :class:`CommandOutcome`. A malformed line is a hard parse error;
  there is no partial credit, no default-success fallback, and no
  "close enough" coercion.

The contract is additive: structural readiness continues to be the
authoritative gate for caching. The three dimensions are
*supplementary* signals surfaced alongside the existing status, never
substitutes for it.
"""

from __future__ import annotations

import json
import re

from ralph.project_policy._assessment_error import EvidenceParseError
from ralph.project_policy._command_outcome import CommandOutcome

# Strict surface markers. Keep these string literals in lockstep with the
# analysis-prompt convention documented in
# ``ralph/policy/defaults/prompts/policy_remediation_analysis.jinja``.
_EVIDENCE_LINE_PREFIX: str = "RALPH-EVIDENCE:"

# Allowed JSON value types. We accept only a small flat object: any extra
# key raises a strict parse error so the producer cannot smuggle a
# self-justifying field past the consumer.
_REQUIRED_OUTCOME_KEYS: frozenset[str] = frozenset(
    {"command", "exit_code", "duration_seconds", "policy_content_hash"}
)
_ALLOWED_OUTCOME_KEYS: frozenset[str] = _REQUIRED_OUTCOME_KEYS
_MAX_EVIDENCE_LINES: int = 64
_MAX_COMMAND_LENGTH: int = 4096
_MAX_HASH_LENGTH: int = 128
_MAX_EXIT_CODE: int = 255

_EVIDENCE_LINE_RE = re.compile(r"^RALPH-EVIDENCE:\s*(.*?)\s*$")


def _parse_single_line(line: str) -> CommandOutcome:
    """Parse one ``RALPH-EVIDENCE:`` line into a :class:`CommandOutcome`.

    The JSON payload MUST be a flat object whose keys are exactly
    :data:`_REQUIRED_OUTCOME_KEYS`. Every value is type-checked; any
    deviation raises :class:`EvidenceParseError`.
    """
    match = _EVIDENCE_LINE_RE.match(line)
    if match is None:
        raise EvidenceParseError(f"line is not a well-formed RALPH-EVIDENCE: line: {line[:80]!r}")
    payload_text = match.group(1)
    try:
        payload: object = json.loads(payload_text)
    except json.JSONDecodeError as exc:
        raise EvidenceParseError(f"RALPH-EVIDENCE JSON payload is invalid: {exc.msg}") from exc
    if not isinstance(payload, dict):
        raise EvidenceParseError("RALPH-EVIDENCE payload must be a JSON object")
    typed_payload: dict[str, object] = payload
    keys = set(typed_payload.keys())
    unknown = keys - _ALLOWED_OUTCOME_KEYS
    if unknown:
        raise EvidenceParseError(f"RALPH-EVIDENCE payload has unknown keys: {sorted(unknown)[0]}")
    missing = _REQUIRED_OUTCOME_KEYS - keys
    if missing:
        raise EvidenceParseError(
            f"RALPH-EVIDENCE payload is missing required key: {sorted(missing)[0]}"
        )
    command = typed_payload["command"]
    if not isinstance(command, str) or not command.strip():
        raise EvidenceParseError("RALPH-EVIDENCE payload requires a non-empty string command")
    if len(command) > _MAX_COMMAND_LENGTH:
        raise EvidenceParseError(
            f"RALPH-EVIDENCE payload command exceeds {_MAX_COMMAND_LENGTH} characters"
        )
    exit_code = typed_payload["exit_code"]
    if isinstance(exit_code, bool) or not isinstance(exit_code, int):
        raise EvidenceParseError("RALPH-EVIDENCE payload requires integer exit_code")
    if exit_code < 0 or exit_code > _MAX_EXIT_CODE:
        raise EvidenceParseError(
            f"RALPH-EVIDENCE payload exit_code {exit_code} is outside the 0..255 range"
        )
    duration = typed_payload["duration_seconds"]
    if isinstance(duration, bool) or not isinstance(duration, (int, float)):
        raise EvidenceParseError("RALPH-EVIDENCE payload requires numeric duration_seconds")
    if duration < 0:
        raise EvidenceParseError("RALPH-EVIDENCE payload duration_seconds must be non-negative")
    content_hash = typed_payload["policy_content_hash"]
    if not isinstance(content_hash, str) or not content_hash.strip():
        raise EvidenceParseError(
            "RALPH-EVIDENCE payload requires a non-empty string policy_content_hash"
        )
    if len(content_hash) > _MAX_HASH_LENGTH:
        raise EvidenceParseError(
            f"RALPH-EVIDENCE payload policy_content_hash exceeds {_MAX_HASH_LENGTH} characters"
        )
    return CommandOutcome(
        command=command.strip(),
        exit_code=exit_code,
        duration_seconds=float(duration),
        policy_content_hash=content_hash.strip(),
    )


def parse_execution_evidence(text: str) -> tuple[CommandOutcome, ...]:
    """Parse the ``RALPH-EVIDENCE:`` lines in ``text`` into outcomes.

    The function is strict: a single malformed line raises
    :class:`EvidenceParseError`. Blank lines and lines that do NOT
    start with the ``RALPH-EVIDENCE:`` prefix are ignored (so the
    artifact's prose body -- headings, summaries, criterion verdicts --
    can live in the same document). The order of the returned outcomes
    preserves the line order in ``text`` so consumers can correlate
    each outcome with the producer's intent.
    """
    outcomes: list[CommandOutcome] = []
    for line in text.splitlines():
        if not line.startswith(_EVIDENCE_LINE_PREFIX):
            continue
        outcomes.append(_parse_single_line(line))
        if len(outcomes) > _MAX_EVIDENCE_LINES:
            raise EvidenceParseError(f"RALPH-EVIDENCE ledger exceeds {_MAX_EVIDENCE_LINES} lines")
    return tuple(outcomes)


def extract_evidence_section(artifact_text: str) -> str:
    """Return the slice of ``artifact_text`` under the ``## Execution evidence`` heading.

    The analysis agent appends a Markdown section by that exact name and
    writes the ``RALPH-EVIDENCE:`` ledger under it. The section is
    optional in the artifact shape (artifacts without it simply yield an
    empty string), so callers pass the result directly to
    :func:`parse_execution_evidence` and let the strict parser decide
    whether the lines are valid.
    """
    lines = artifact_text.splitlines()
    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip().lower() == "## execution evidence":
            start_idx = idx + 1
            break
    if start_idx is None:
        return ""
    end_idx = len(lines)
    for idx in range(start_idx, len(lines)):
        line = lines[idx]
        if line.startswith("## ") and idx > start_idx:
            end_idx = idx
            break
    return "\n".join(lines[start_idx:end_idx])


__all__ = [
    "extract_evidence_section",
    "parse_execution_evidence",
]

"""Deterministic project-policy validator.

This module is the ONLY authority on whether a project is policy-ready.
There is no AI, no network, no prose NLP — every check is a structural
pattern match against the canonical file content. Each failure emits a
:class:`~ralph.project_policy.models.PolicyFinding` with a stable
``requirement_id`` (one of the ``ID_*`` constants in
:mod:`ralph.project_policy.markers`), the affected path, the missing
evidence, and the required remediation outcome.

The validator is the consumer of the shared readiness-evidence inventory
in :mod:`ralph.project_policy.evidence`. The inventory enumerates every
file the validator needs to read; the cache hashes the same inventory so
the cache cannot drift from the validator.

A project is ready iff ``validate_readiness`` returns an empty list.
"""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

from ralph.project_policy import evidence, markers
from ralph.project_policy.models import PolicyFinding

if TYPE_CHECKING:
    from ralph.language_detector.models import ProjectStack
    from ralph.workspace.protocol import Workspace

# Regex helpers: line-prefixed field markers. The validator slices content
# into lines and looks at the start of each line; this avoids prose NLP.
# The command/inapplicable regexes MATCH empty values too so the validator
# can surface a stable finding for the offending line; a separate ``*_VALUE``
# regex (requires ``\S+``) gates whether the value is treated as a real
# declaration. An empty marker is a documented acceptance violation that
# must emit a finding — it is NOT silently accepted.
_FACT_LINE_RE = re.compile(r"^RALPH-FACT:\s*\S+")
_FACT_VALUE_RE = re.compile(r"^RALPH-FACT:\s*\S.*?\S\s*$")
_COMMAND_LINE_RE = re.compile(r"^RALPH-COMMAND:.*$")
_COMMAND_VALUE_RE = re.compile(r"^RALPH-COMMAND:\s*\S+.*$")
_INAPPLICABLE_LINE_RE = re.compile(r"^RALPH-INAPPLICABLE:.*$")
_INAPPLICABLE_VALUE_RE = re.compile(r"^RALPH-INAPPLICABLE:\s*\S+.*$")
_LANG_LINE_RE = re.compile(r"^RALPH-LANG:\s*(\S.*?)\s*$")
_HEADING_LINE_RE = re.compile(r"^\s*#{1,2}\s+(.+?)\s*$")
_POLICY_ID_LINE_RE = re.compile(r"<!--\s*ralph-policy-id:\s*([^>\s]+)\s*-->")


def _has_any(content: str, substrings: tuple[str, ...]) -> str | None:
    """Return the first matching substring in ``content`` or None."""
    for sub in substrings:
        if sub in content:
            return sub
    return None


def _contains_placeholder(content: str) -> str | None:
    """Return the first placeholder token found in ``content`` or None."""
    return _has_any(content, markers.PLACEHOLDER_TOKENS)


def _command_values(content: str) -> list[str]:
    """Return the trimmed command values from every *valid* RALPH-COMMAND line.

    A "valid" command line is one with a non-whitespace value (regex
    :data:`_COMMAND_VALUE_RE`). Empty command lines are still parsed by
    :func:`_check_individual_commands` so the user sees a precise finding
    with the offending line number.
    """
    values: list[str] = []
    for line in content.splitlines():
        match = _COMMAND_VALUE_RE.match(line)
        if match is not None:
            value = line[len(markers.COMMAND_MARKER):].strip()
            values.append(value)
    return values


def _command_raw_lines(content: str) -> list[str]:
    """Return every ``RALPH-COMMAND:`` line (including empty-value lines).

    Used by :func:`_check_individual_commands` to surface findings for
    empty-value command lines. The list preserves the original ordering so
    the user sees the offending line at a stable index.
    """
    return [
        line[len(markers.COMMAND_MARKER):].strip()
        for line in content.splitlines()
        if _COMMAND_LINE_RE.match(line)
    ]


def _inapplicable_lines(content: str) -> list[str]:
    """Return the trimmed values of every *valid* RALPH-INAPPLICABLE line.

    A "valid" inapplicable line is one with a non-whitespace value (regex
    :data:`_INAPPLICABLE_VALUE_RE`). Empty inapplicable lines are still
    parsed by :func:`_check_individual_inapplicables` so the user sees a
    precise finding with the offending line number.
    """
    return [
        line[len(markers.INAPPLICABLE_MARKER):].strip()
        for line in content.splitlines()
        if _INAPPLICABLE_VALUE_RE.match(line)
    ]


def _inapplicable_raw_lines(content: str) -> list[str]:
    """Return every ``RALPH-INAPPLICABLE:`` line (including empty-value lines).

    Used by :func:`_check_individual_inapplicables` to surface findings
    for empty-value inapplicable lines.
    """
    return [
        line[len(markers.INAPPLICABLE_MARKER):].strip()
        for line in content.splitlines()
        if _INAPPLICABLE_LINE_RE.match(line)
    ]


def _inapplicable_present(content: str) -> bool:
    """Return True when at least one non-empty RALPH-INAPPLICABLE line exists."""
    return bool(_inapplicable_lines(content))


def _inapplicable_lines_for_lang_block(block_lines: list[str]) -> list[str]:
    """Return the trimmed RALPH-INAPPLICABLE values inside one per-language block.

    Empty ``RALPH-INAPPLICABLE:`` lines inside a per-language block are
    excluded here; they are flagged separately in
    :func:`_check_per_language_coverage`.
    """
    joined = "\n".join(block_lines)
    return _inapplicable_lines(joined)


def _fact_lines(content: str) -> list[str]:
    """Return the trimmed RALPH-FACT lines (without the marker).

    Empty ``RALPH-FACT:`` lines (only the marker + whitespace) are excluded
    here so :func:`_check_placeholders` can distinguish a missing-fact file
    from a file that contains only structurally-empty markers.
    """
    out: list[str] = []
    for line in content.splitlines():
        if not _FACT_VALUE_RE.match(line):
            continue
        out.append(line[len(markers.FACT_MARKER):].strip())
    return out


def _lang_blocks(content: str) -> dict[str, tuple[bool, bool]]:
    """Return a mapping of language name -> (has_command, has_inapplicable).

    The validator slices the file into per-language blocks delimited by
    ``RALPH-LANG:`` lines. Each block ends at the next RALPH-LANG line OR
    the end of the file. A block must contain at least one non-empty
    RALPH-COMMAND or RALPH-INAPPLICABLE line to satisfy per-language
    coverage — empty marker lines are NOT counted as a declaration.
    """
    blocks: dict[str, list[str]] = {}
    current_lang: str | None = None
    for line in content.splitlines():
        lang_match = _LANG_LINE_RE.match(line)
        if lang_match is not None:
            current_lang = str(lang_match.group(1)).strip()
            if not current_lang:
                current_lang = ""
            blocks.setdefault(current_lang, [])
            continue
        if current_lang is not None:
            blocks[current_lang].append(line)
    out: dict[str, tuple[bool, bool]] = {}
    for lang, lines in blocks.items():
        out[lang] = (
            bool(_command_values("\n".join(lines))),
            bool(_inapplicable_lines_for_lang_block(lines)),
        )
    return out


def _headings(content: str) -> set[str]:
    """Return the normalized heading texts in ``content``."""
    headings: set[str] = set()
    for line in content.splitlines():
        match = _HEADING_LINE_RE.match(line)
        if match is not None:
            text = str(match.group(1)).strip().lower()
            headings.add(text)
    return headings


def _citation_blocks(content: str) -> list[str]:
    """Return the per-citation blocks in the Research basis section.

    The validator splits the file at every "## Research basis" heading and
    takes the slice up to the next H2 (or end-of-file). It then treats each
    non-empty paragraph (separated by blank lines) as one citation block
    that must contain every :data:`markers.CITATION_REQUIRED_FIELDS` token.
    """
    lines = content.splitlines()
    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip().lower() == "## research basis":
            start_idx = idx + 1
            break
    if start_idx is None:
        # No research basis section: caller will surface a separate finding.
        return []
    end_idx = len(lines)
    for idx in range(start_idx, len(lines)):
        line = lines[idx]
        if line.startswith("## ") and idx > start_idx:
            end_idx = idx
            break
    section_lines = lines[start_idx:end_idx]
    blocks: list[str] = []
    current: list[str] = []
    for line in section_lines:
        if not line.strip():
            if current:
                blocks.append("\n".join(current).strip())
                current = []
        else:
            current.append(line)
    if current:
        blocks.append("\n".join(current).strip())
    return [b for b in blocks if b]


def _check_agents_md(workspace: Workspace) -> list[PolicyFinding]:
    """Validate the AGENTS.md contract (managed block + canonical-dir reference).

    The managed block contract is unique-count: exactly one
    :data:`markers.AGENTS_BLOCK_BEGIN` AND exactly one
    :data:`markers.AGENTS_BLOCK_END` must appear, the begin must precede the
    end, and the block body must reference :data:`markers.CANONICAL_DIR`. A
    duplicate begin/end, mismatched counts, or a begin-after-end pairing is
    rejected with a stable ``RWP-MARKER:agents-block-*`` finding so the
    remediation agent must reconcile the file before development can begin.
    """
    findings: list[PolicyFinding] = []
    if not workspace.exists(markers.AGENTS_MD):
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_AGENTS_MD_MISSING}:missing",
                path=markers.AGENTS_MD,
                missing_evidence="AGENTS.md is missing",
                required_outcome="create AGENTS.md containing the managed Ralph Workflow block",
            )
        ]
    content = workspace.read(markers.AGENTS_MD)
    findings.extend(_check_managed_block_uniqueness(content))
    if not findings:
        # Only check the canonical-dir reference when the managed block is
        # structurally valid (unique begin/end + begin before end). A
        # malformed block already emits its own finding; adding a
        # canonical-dir-ref finding on top of multiple marker findings would
        # bury the actionable defect.
        findings.extend(_check_managed_block_canonical_dir(content))
    return findings


def _check_managed_block_uniqueness(content: str) -> list[PolicyFinding]:
    """Validate the managed block has exactly one begin AND exactly one end.

    Emits a stable RWP-MARKER:agents-block-* finding for every malformed
    case so the user sees precise pointers for duplicates, missing halves,
    or begin-after-end pairing.
    """
    findings: list[PolicyFinding] = []
    begin_count = content.count(markers.AGENTS_BLOCK_BEGIN)
    end_count = content.count(markers.AGENTS_BLOCK_END)
    begin_idx = content.find(markers.AGENTS_BLOCK_BEGIN)
    end_idx = content.find(markers.AGENTS_BLOCK_END)
    if begin_count == 0 and end_count == 0:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:agents-block:missing",
                path=markers.AGENTS_MD,
                missing_evidence=(
                    f"missing managed block markers {markers.AGENTS_BLOCK_BEGIN} "
                    f"/ {markers.AGENTS_BLOCK_END}"
                ),
                required_outcome=(
                    "append exactly one managed Ralph Workflow block "
                    "(preserve all existing content)"
                ),
            )
        )
        return findings
    if begin_count == 0 or end_count == 0:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:agents-block:unmatched",
                path=markers.AGENTS_MD,
                missing_evidence=(
                    f"unmatched managed block markers "
                    f"(begin={begin_count}, end={end_count}); "
                    "expected exactly one of each"
                ),
                required_outcome=(
                    "remove the partial managed block and append exactly one "
                    f"complete pair ({markers.AGENTS_BLOCK_BEGIN} ... "
                    f"{markers.AGENTS_BLOCK_END}) preserving user content"
                ),
            )
        )
        return findings
    if begin_count > 1 or end_count > 1:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:agents-block:duplicate",
                path=markers.AGENTS_MD,
                missing_evidence=(
                    f"duplicate managed block markers "
                    f"(begin={begin_count}, end={end_count}); expected "
                    "exactly one of each"
                ),
                required_outcome=(
                    "consolidate to exactly one managed block: remove all "
                    "duplicate Ralph-managed begin/end markers while "
                    "preserving any user-authored content outside the "
                    "managed block"
                ),
            )
        )
        return findings
    if begin_idx > end_idx:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:agents-block:misordered",
                path=markers.AGENTS_MD,
                missing_evidence=(
                    f"managed block end marker appears before begin marker; "
                    f"expected {markers.AGENTS_BLOCK_BEGIN} to precede "
                    f"{markers.AGENTS_BLOCK_END}"
                ),
                required_outcome=(
                    "restore the managed block order so the begin marker "
                    "precedes the end marker; remove any duplicate or "
                    "out-of-order Ralph-managed markers"
                ),
            )
        )
    return findings


def _extract_managed_block_body(content: str) -> str | None:
    """Return the body text strictly between the unique begin/end markers.

    The validator slices AGENTS.md between the validated unique
    :data:`markers.AGENTS_BLOCK_BEGIN` and
    :data:`markers.AGENTS_BLOCK_END` markers and returns the body. A
    :keyword:`None` return means the block is malformed (no unique pair)
    and the caller has already emitted its own marker finding; this helper
    is a no-op for malformed blocks so the canonical-dir gate does not
    produce duplicate or buried findings.
    """
    if content.count(markers.AGENTS_BLOCK_BEGIN) != 1:
        return None
    if content.count(markers.AGENTS_BLOCK_END) != 1:
        return None
    begin_idx = content.find(markers.AGENTS_BLOCK_BEGIN)
    end_idx = content.find(markers.AGENTS_BLOCK_END)
    if begin_idx >= end_idx:
        return None
    return content[begin_idx + len(markers.AGENTS_BLOCK_BEGIN):end_idx]


def _check_managed_block_canonical_dir(content: str) -> list[PolicyFinding]:
    """Validate the managed block BODY references the canonical policy dir.

    The check is scoped strictly to the body sliced between the validated
    unique begin/end markers so a CANONICAL_DIR reference OUTSIDE the
    managed block (e.g. in user-authored prose before or after the block)
    cannot satisfy the gate. A malformed block returns no finding here
    because :func:`_check_managed_block_uniqueness` already surfaces the
    precise marker finding.
    """
    body = _extract_managed_block_body(content)
    if body is None:
        return []
    if markers.CANONICAL_DIR in body:
        return []
    return [
        PolicyFinding(
            requirement_id=f"{markers.ID_MARKER_MISSING}:canonical-dir-ref",
            path=markers.AGENTS_MD,
            missing_evidence=(
                f"managed block body does not reference canonical policy "
                f"dir {markers.CANONICAL_DIR} (reference outside the block "
                "does not satisfy the gate)"
            ),
            required_outcome=(
                f"add a reference to {markers.CANONICAL_DIR} INSIDE the "
                f"managed block between {markers.AGENTS_BLOCK_BEGIN} and "
                f"{markers.AGENTS_BLOCK_END}"
            ),
        )
    ]


def _check_claude_md(workspace: Workspace) -> list[PolicyFinding]:
    """Validate the CLAUDE.md contract (AGENTS.md pointer when CLAUDE.md exists)."""
    if not workspace.exists(markers.CLAUDE_MD):
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_CLAUDE_MD_MISSING}:missing",
                path=markers.CLAUDE_MD,
                missing_evidence="CLAUDE.md is missing",
                required_outcome=f"create CLAUDE.md pointing Claude-compatible agents at {markers.AGENTS_MD}",
            )
        ]
    content = workspace.read(markers.CLAUDE_MD)
    if "AGENTS.md" not in content:
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:claude-agents-ref",
                path=markers.CLAUDE_MD,
                missing_evidence=f"CLAUDE.md does not reference {markers.AGENTS_MD}",
                required_outcome=f"append a pointer from CLAUDE.md to {markers.AGENTS_MD}",
            )
        ]
    return []


def _check_core_policy_file(
    workspace: Workspace, filename: str
) -> list[PolicyFinding]:
    """Validate one canonical core policy file."""
    path = f"{markers.CANONICAL_DIR}{filename}"
    return _check_policy_file(workspace, path, filename)


def _check_policy_file(workspace: Workspace, path: str, filename: str) -> list[PolicyFinding]:
    """Validate one canonical policy file (presence, structure, fields, completion).

    Composed from small focused helpers to keep the branch count low.
    """
    if not workspace.exists(path):
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_CORE_MISSING}:{filename}",
                path=path,
                missing_evidence=f"file does not exist: {path}",
                required_outcome=f"create the canonical policy file at {path}",
            )
        ]
    content = workspace.read(path)
    return _validate_existing_policy_file(content, path, filename)


def _validate_existing_policy_file(
    content: str, path: str, filename: str
) -> list[PolicyFinding]:
    """Validate every check that operates on an existing policy file's content."""
    findings: list[PolicyFinding] = []
    findings.extend(_check_markers(content, path, filename))
    findings.extend(_check_required_headings(content, path, filename))
    findings.extend(_check_research_basis(content, path, filename))
    findings.extend(_check_placeholders(content, path, filename))
    findings.extend(_check_commands(content, path, filename))
    findings.extend(_check_completion_marker(content, path, filename))
    return findings


def _check_markers(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate the schema + policy-id markers."""
    findings: list[PolicyFinding] = []
    if markers.POLICY_SCHEMA_MARKER not in content:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:schema-{filename}",
                path=path,
                missing_evidence=f"missing schema marker {markers.POLICY_SCHEMA_MARKER}",
                required_outcome="add the schema marker comment at the top of the file",
            )
        )
    expected_id = f"{markers.POLICY_ID_PREFIX} {filename} -->"
    if expected_id not in content:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MARKER_MISSING}:id-{filename}",
                path=path,
                missing_evidence=f"missing policy-id line: {expected_id}",
                required_outcome=(
                    f"add `<!-- ralph-policy-id: {filename} -->` comment to the file"
                ),
            )
        )
    return findings


def _check_required_headings(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate every required heading is present (case-insensitive)."""
    required = markers.REQUIRED_HEADINGS.get(filename, ())
    headings_present = _headings(content)
    return [
        PolicyFinding(
            requirement_id=f"{markers.ID_HEADING_MISSING}:{filename}:{required_heading}",
            path=path,
            missing_evidence=f"missing required heading '{required_heading}'",
            required_outcome=f"add a heading with the exact text '{required_heading}'",
        )
        for required_heading in required
        if required_heading.lower() not in headings_present
    ]


def _check_research_basis(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate the Research basis section exists and every citation is complete."""
    findings: list[PolicyFinding] = []
    headings_lower = {h.lower() for h in _headings(content)}
    if "research basis" not in headings_lower:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_HEADING_MISSING}:{filename}:research-basis",
                path=path,
                missing_evidence="Research basis section is missing",
                required_outcome="add a 'Research basis' heading with at least one citation",
            )
        )
        return findings
    blocks = _citation_blocks(content)
    if not blocks:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_CITATION_MISSING}:{filename}:empty",
                path=path,
                missing_evidence=(
                    "Research basis section has no citations; "
                    f"each citation must include {', '.join(markers.CITATION_REQUIRED_FIELDS)}"
                ),
                required_outcome=(
                    "add at least one citation under Research basis with "
                    "publisher, title, URL (http), and review date"
                ),
            )
        )
        return findings
    findings.extend(_check_individual_citations(blocks, path, filename))
    return findings


def _check_individual_citations(
    blocks: list[str], path: str, filename: str
) -> list[PolicyFinding]:
    """Validate every citation block contains the required fields."""
    findings: list[PolicyFinding] = []
    for index, block in enumerate(blocks, start=1):
        missing = [
            field
            for field in markers.CITATION_REQUIRED_FIELDS
            if field.lower() not in block.lower()
        ]
        if not missing:
            continue
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_CITATION_MISSING}:{filename}:block-{index}",
                path=path,
                missing_evidence=(
                    f"Research basis citation {index} missing field(s): {missing}"
                ),
                required_outcome=(
                    f"add missing fields {missing} to citation {index} "
                    f"(publisher, title, http URL, review date)"
                ),
            )
        )
    return findings


def _check_placeholders(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate every RALPH-FACT line is resolved and at least one exists.

    A complete policy must declare at least one resolved ``RALPH-FACT:``
    line — the validator rejects generic prose that is missing machine-
    checkable project facts. Placeholder tokens anywhere in the file are
    rejected (the contract is project-specific content, not template
    copy).
    """
    findings: list[PolicyFinding] = []
    placeholder = _contains_placeholder(content)
    if placeholder is not None:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_PLACEHOLDER}:{filename}",
                path=path,
                missing_evidence=f"unresolved placeholder token: {placeholder}",
                required_outcome=f"replace '{placeholder}' with verified project fact",
            )
        )
    fact_lines = _fact_lines(content)
    if not fact_lines:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_PLACEHOLDER}:{filename}:no-fact",
                path=path,
                missing_evidence=(
                    "policy file declares no resolved RALPH-FACT line; every "
                    "project-specific policy must enumerate its machine-"
                    "checkable project facts (test commands, paths, "
                    "owners, budgets, supported platforms, etc.) as "
                    "`RALPH-FACT: <key>: <value>` lines"
                ),
                required_outcome=(
                    "add at least one resolved `RALPH-FACT:` line naming a "
                    "verified project-specific fact; replace any starter "
                    "placeholder with a real value before removing this finding"
                ),
            )
        )
    for line in content.splitlines():
        if not line.startswith(markers.FACT_MARKER):
            continue
        for placeholder_token in markers.PLACEHOLDER_TOKENS:
            if placeholder_token in line:
                findings.append(
                    PolicyFinding(
                        requirement_id=f"{markers.ID_PLACEHOLDER}:{filename}:fact-line",
                        path=path,
                        missing_evidence=(
                            f"RALPH-FACT line contains placeholder '{placeholder_token}'"
                        ),
                        required_outcome="resolve every RALPH-FACT line with a verified value",
                    )
                )
                break
    return findings


def _check_commands(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate at least one non-empty command OR inapplicable marker exists.

    Empty ``RALPH-COMMAND:`` and ``RALPH-INAPPLICABLE:`` lines are explicitly
    rejected so a policy cannot exempt itself from the gate with an empty
    marker. The general inapplicability value must name a reason (e.g. why
    the verification step does not apply to this project).
    """
    findings: list[PolicyFinding] = []
    command_values = _command_values(content)
    command_raw_values = _command_raw_lines(content)
    inapplicable_values = _inapplicable_lines(content)
    inapplicable_raw_values = _inapplicable_raw_lines(content)
    has_command = bool(command_values)
    has_inapplicable = bool(inapplicable_values)
    if not has_command and not has_inapplicable:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_CMD_UNUSABLE}:{filename}:missing",
                path=path,
                missing_evidence=(
                    "file contains neither a non-empty RALPH-COMMAND nor a "
                    "non-empty RALPH-INAPPLICABLE line"
                ),
                required_outcome=(
                    "add at least one RALPH-COMMAND line with a real runnable "
                    "verification command (or RALPH-INAPPLICABLE with a reason)"
                ),
            )
        )
    findings.extend(_check_individual_commands(command_raw_values, path, filename))
    findings.extend(
        _check_individual_inapplicables(inapplicable_raw_values, path, filename)
    )
    return findings


def _check_individual_inapplicables(
    inapplicable_values: list[str], path: str, filename: str
) -> list[PolicyFinding]:
    """Validate every RALPH-INAPPLICABLE value is non-empty and placeholder-free.

    The parser filters empty lines before reaching this helper, but
    ``_fact_lines``/``_inapplicable_lines`` also enforce the
    non-whitespace-value contract via regex. A no-op marker (the bare
    ``RALPH-INAPPLICABLE:`` token with only whitespace after the colon)
    MUST produce a stable RWP-CMD unusable finding so the user sees a
    precise pointer at the offending line.
    """
    findings: list[PolicyFinding] = []
    for index, value in enumerate(inapplicable_values, start=1):
        if not value:
            findings.append(
                PolicyFinding(
                    requirement_id=(
                        f"{markers.ID_CMD_UNUSABLE}:{filename}:empty-inapplicable-{index}"
                    ),
                    path=path,
                    missing_evidence=(
                        f"RALPH-INAPPLICABLE line {index} is empty; the marker "
                        "must declare a reason or remove the line"
                    ),
                    required_outcome=(
                        "add the reason the gate does not apply "
                        "(e.g. 'no graphical surface in CI') or replace the "
                        "line with a real RALPH-COMMAND"
                    ),
                )
            )
            continue
        for placeholder_token in markers.PLACEHOLDER_TOKENS:
            if placeholder_token in value:
                findings.append(
                    PolicyFinding(
                        requirement_id=(
                            f"{markers.ID_CMD_UNUSABLE}:{filename}:placeholder-inapplicable-{index}"
                        ),
                        path=path,
                        missing_evidence=(
                            f"RALPH-INAPPLICABLE line {index} contains placeholder "
                            f"token '{placeholder_token}'"
                        ),
                        required_outcome=(
                            "replace the placeholder in RALPH-INAPPLICABLE with "
                            "the real reason the gate does not apply"
                        ),
                    )
                )
                break
    return findings


def _command_first_token(value: str) -> str:
    """Return the first whitespace-separated token of a command value.

    Splits on ASCII whitespace; an all-whitespace value returns an empty
    string. The validator uses this to look up the executable against the
    fixed :data:`markers.APPROVED_GATE_TOOLS` allowlist so a non-empty
    value like ``definitely-not-a-command`` (analysis-feedback repro) is
    rejected deterministically without consulting an AI.
    """
    stripped = value.strip()
    if not stripped:
        return ""
    return stripped.split(None, 1)[0]


def _command_is_approved(value: str) -> bool:
    """Return True when the command's first token is on the approved allowlist.

    The allowlist is the deterministic, machine-checkable command contract
    required by the analysis feedback: a declared gate is "usable" iff its
    first whitespace-separated token names a known gate executable. This
    closure replaces the previous "non-empty and placeholder-free" check
    that accepted arbitrary text such as ``definitely-not-a-command``.
    """
    first = _command_first_token(value)
    if not first:
        return False
    return first in markers.APPROVED_GATE_TOOLS


def _check_individual_commands(
    command_values: list[str], path: str, filename: str
) -> list[PolicyFinding]:
    """Validate every RALPH-COMMAND value is non-empty, placeholder-free, and approved.

    The check enforces three independent gates:

    1. The value is non-empty (an empty ``RALPH-COMMAND:`` line is not a
       valid gate).
    2. The value contains no placeholder token (template copy is not a
       valid gate).
    3. The value's first whitespace-separated token is on the fixed
       :data:`markers.APPROVED_GATE_TOOLS` allowlist (analysis feedback:
       arbitrary non-empty text such as ``definitely-not-a-command`` does
       NOT satisfy the executable-gate contract; the validator must
       reject it with a stable ``RWP-CMD:*:unapproved-cmd-N`` finding).

    A failing gate produces a stable ``RWP-CMD:filename:<kind>-N``
    finding so the user sees a precise pointer at the offending line.
    """
    findings: list[PolicyFinding] = []
    for index, value in enumerate(command_values, start=1):
        if not value:
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_CMD_UNUSABLE}:{filename}:empty-cmd-{index}",
                    path=path,
                    missing_evidence=f"RALPH-COMMAND line {index} is empty",
                    required_outcome="remove the empty command or replace it with a runnable command",
                )
            )
            continue
        for placeholder_token in markers.PLACEHOLDER_TOKENS:
            if placeholder_token in value:
                findings.append(
                    PolicyFinding(
                        requirement_id=(
                            f"{markers.ID_CMD_UNUSABLE}:{filename}:placeholder-cmd-{index}"
                        ),
                        path=path,
                        missing_evidence=(
                            f"RALPH-COMMAND line {index} contains placeholder "
                            f"token '{placeholder_token}'"
                        ),
                        required_outcome=(
                            "replace the placeholder in RALPH-COMMAND with the "
                            "real runnable command"
                        ),
                    )
                )
                break
        else:
            # Only run the approved-tools check when neither empty nor
            # placeholder fired (the per-line ``else`` belongs to the
            # ``for`` loop and runs when no ``break`` was taken, i.e. no
            # placeholder matched). This keeps the findings list minimal:
            # a placeholder command gets the placeholder finding, not an
            # additional unapproved-tools finding on top of it.
            if not _command_is_approved(value):
                first_token = _command_first_token(value)
                findings.append(
                    PolicyFinding(
                        requirement_id=(
                            f"{markers.ID_CMD_UNUSABLE}:{filename}:unapproved-cmd-{index}"
                        ),
                        path=path,
                        missing_evidence=(
                            f"RALPH-COMMAND line {index} starts with "
                            f"'{first_token}', which is not in the "
                            "approved gate-tools allowlist; declared "
                            "gates MUST be executable verification "
                            "commands (analysis feedback regression)"
                        ),
                        required_outcome=(
                            "replace the command with one whose first "
                            "token is on the approved gate-tools "
                            "allowlist (e.g. `make <target>`, `pytest`, "
                            "`mypy`, `ruff`, `cargo`, `go`, `npm`, `pnpm`, "
                            "`yarn`, `uv run`, ...) or declare the gate "
                            "inapplicable via a `RALPH-INAPPLICABLE:` line"
                        ),
                    )
                )
    return findings


def _check_completion_marker(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate the completion marker is present."""
    if markers.COMPLETION_MARKER in content:
        return []
    return [
        PolicyFinding(
            requirement_id=f"{markers.ID_COMPLETION_MISSING}:{filename}",
            path=path,
            missing_evidence=(
                f"missing completion marker {markers.COMPLETION_MARKER}"
            ),
            required_outcome=(
                "add the completion marker comment ONLY when every other "
                "requirement is satisfied and the file is verified project-specific"
            ),
        )
    ]


def _check_per_language_coverage(
    workspace: Workspace, stack: ProjectStack
) -> list[PolicyFinding]:
    """Validate per-language RALPH-LANG coverage in typecheck and lint policies.

    Per-language blocks must contain a non-empty ``RALPH-COMMAND`` or
    ``RALPH-INAPPLICABLE`` line. An empty inapplicable marker is treated
    as a missing declaration and produces a stable
    ``RWP-LANG`` finding; a no-op exemption cannot silently disable a
    per-language gate.
    """
    required_langs = evidence.required_languages(stack)
    if not required_langs:
        return []
    findings: list[PolicyFinding] = []
    for filename in ("typechecking-policy.md", "linting-policy.md"):
        path = f"{markers.CANONICAL_DIR}{filename}"
        if not workspace.exists(path):
            continue  # core-file presence check already emitted a finding.
        content = workspace.read(path)
        blocks = _lang_blocks(content)
        # Detect empty ``RALPH-INAPPLICABLE:`` lines inside per-language
        # blocks (the parser strips them from the boolean count above).
        empty_lang_inapplicable = _find_empty_per_language_inapplicable(content)
        for language in sorted(required_langs):
            if language not in blocks:
                findings.append(
                    PolicyFinding(
                        requirement_id=f"{markers.ID_LANG_COVERAGE}:{filename}:{language}",
                        path=path,
                        missing_evidence=(
                            f"no RALPH-LANG block for '{language}' in {filename}"
                        ),
                        required_outcome=(
                            f"add `RALPH-LANG: {language}` followed by a "
                            "RALPH-COMMAND or RALPH-INAPPLICABLE line for this language"
                        ),
                    )
                )
                continue
            has_command, has_inapplicable = blocks[language]
            if not has_command and not has_inapplicable:
                findings.append(
                    PolicyFinding(
                        requirement_id=f"{markers.ID_LANG_COVERAGE}:{filename}:{language}:empty",
                        path=path,
                        missing_evidence=(
                            f"RALPH-LANG block for '{language}' has neither "
                            "a non-empty RALPH-COMMAND nor a non-empty "
                            "RALPH-INAPPLICABLE"
                        ),
                        required_outcome=(
                            f"add a non-empty RALPH-COMMAND or "
                            f"RALPH-INAPPLICABLE line after `RALPH-LANG: {language}`"
                        ),
                    )
                )
            if language in empty_lang_inapplicable:
                findings.append(
                    PolicyFinding(
                        requirement_id=(
                            f"{markers.ID_LANG_COVERAGE}:{filename}:{language}:"
                            f"empty-inapplicable"
                        ),
                        path=path,
                        missing_evidence=(
                            f"RALPH-LANG block for '{language}' contains an "
                            "empty RALPH-INAPPLICABLE line; the marker must "
                            "declare a reason or be replaced by RALPH-COMMAND"
                        ),
                        required_outcome=(
                            f"add a real reason after `RALPH-INAPPLICABLE:` in "
                            f"the `{language}` block, or remove the empty "
                            "marker and add a runnable RALPH-COMMAND instead"
                        ),
                    )
                )
    return findings


def _find_empty_per_language_inapplicable(content: str) -> set[str]:
    """Return the set of language names whose block contains an empty
    ``RALPH-INAPPLICABLE:`` line.

    An empty inapplicable marker is a structural defect: it claims
    inapplicability without justification, so it MUST produce a finding
    even when the language block also contains a usable command.
    """
    out: set[str] = set()
    current_lang: str | None = None
    for line in content.splitlines():
        lang_match = _LANG_LINE_RE.match(line)
        if lang_match is not None:
            current_lang = str(lang_match.group(1)).strip()
            continue
        if current_lang is None:
            continue
        stripped = line[len(markers.INAPPLICABLE_MARKER):].strip() if line.startswith(
            markers.INAPPLICABLE_MARKER
        ) else None
        if line.startswith(markers.INAPPLICABLE_MARKER) and not stripped:
            out.add(current_lang)
    return out


def _check_verification_bypass(workspace: Workspace) -> list[PolicyFinding]:
    """Validate the verification-policy bypass-detection gate.

    The gate requires the 'Bypass detection' heading AND at least one
    non-empty, placeholder-free, approved-tools RALPH-COMMAND line under
    it. The check is identical to the per-policy command gate except the
    finding id is namespaced under ``:bypass-cmd:<kind>`` so the user
    sees a precise pointer for an unusable bypass-detection gate:

    * ``:bypass-cmd:empty`` — the only command in the section is empty.
    * ``:bypass-cmd:placeholder`` — the value contains a placeholder
      token.
    * ``:bypass-cmd:unapproved`` — the value's first token is not on the
      fixed :data:`markers.APPROVED_GATE_TOOLS` allowlist (analysis
      feedback regression: arbitrary text such as ``definitely-not-a-command``
      must NOT satisfy the bypass-detection contract).
    * ``:bypass-cmd`` — no ``RALPH-COMMAND:`` line at all.
    """
    path = f"{markers.CANONICAL_DIR}verification-policy.md"
    findings: list[PolicyFinding] = []
    if not workspace.exists(path):
        return findings  # core-file presence check covers it.
    content = workspace.read(path)
    headings_present = _headings(content)
    if "bypass detection" not in headings_present:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_HEADING_MISSING}:verification-policy.md:bypass-detection",
                path=path,
                missing_evidence=(
                    "verification-policy.md is missing the 'Bypass detection' heading"
                ),
                required_outcome=(
                    "add a 'Bypass detection' heading with at least one "
                    "non-empty, placeholder-free RALPH-COMMAND that runs "
                    "the lint/typecheck bypass audit"
                ),
            )
        )
        return findings
    # The bypass-detection block must include at least one non-empty,
    # placeholder-free, approved-tools command. Empty RALPH-COMMAND:
    # markers are NOT accepted; the value after the marker must contain
    # non-whitespace text, no placeholder token, and a first token on the
    # approved allowlist.
    in_section = False
    saw_real_command = False
    saw_empty_command = False
    for line in content.splitlines():
        heading_match = _HEADING_LINE_RE.match(line)
        if heading_match is not None:
            heading_text = str(heading_match.group(1)).strip().lower()
            in_section = heading_text == "bypass detection"
            continue
        if not in_section:
            continue
        if not line.startswith(markers.COMMAND_MARKER):
            continue
        value = line[len(markers.COMMAND_MARKER):].strip()
        if not value:
            saw_empty_command = True
            continue
        if any(token in value for token in markers.PLACEHOLDER_TOKENS):
            findings.append(
                PolicyFinding(
                    requirement_id=(
                        f"{markers.ID_CMD_UNUSABLE}:verification-policy.md:"
                        f"bypass-cmd:placeholder"
                    ),
                    path=path,
                    missing_evidence=(
                        "'Bypass detection' RALPH-COMMAND contains a "
                        "placeholder token"
                    ),
                    required_outcome=(
                        "replace the placeholder in the 'Bypass detection' "
                        "RALPH-COMMAND with a real runnable bypass-audit command"
                    ),
                )
            )
            continue
        if not _command_is_approved(value):
            first_token = _command_first_token(value)
            findings.append(
                PolicyFinding(
                    requirement_id=(
                        f"{markers.ID_CMD_UNUSABLE}:verification-policy.md:"
                        f"bypass-cmd:unapproved"
                    ),
                    path=path,
                    missing_evidence=(
                        f"'Bypass detection' RALPH-COMMAND starts with "
                        f"'{first_token}', which is not in the approved "
                        "gate-tools allowlist; the bypass-audit gate MUST "
                        "be a runnable verification command (analysis "
                        "feedback regression)"
                    ),
                    required_outcome=(
                        "replace the command with one whose first token "
                        "is on the approved gate-tools allowlist (e.g. "
                        "`make <target>`, `pytest`, `mypy`, `ruff`, "
                        "`cargo`, `go`, `npm`, `pnpm`, `yarn`, `uv run`, "
                        "...)"
                    ),
                )
            )
            continue
        saw_real_command = True
    if saw_empty_command and not saw_real_command:
        findings.append(
            PolicyFinding(
                requirement_id=(
                    f"{markers.ID_CMD_UNUSABLE}:verification-policy.md:"
                    f"bypass-cmd:empty"
                ),
                path=path,
                missing_evidence=(
                    "'Bypass detection' section contains only an empty "
                    "RALPH-COMMAND marker; the gate MUST declare a runnable "
                    "non-empty command"
                ),
                required_outcome=(
                    "add a non-empty, placeholder-free RALPH-COMMAND under "
                    "'Bypass detection' that runs the lint/typecheck bypass audit"
                ),
            )
        )
    if not saw_real_command and not saw_empty_command:
        findings.append(
            PolicyFinding(
                requirement_id=(
                    f"{markers.ID_CMD_UNUSABLE}:verification-policy.md:bypass-cmd"
                ),
                path=path,
                missing_evidence=(
                    "'Bypass detection' section has no RALPH-COMMAND line"
                ),
                required_outcome=(
                    "add a non-empty, placeholder-free RALPH-COMMAND under "
                    "'Bypass detection' that runs the bypass-detection audit"
                ),
            )
        )
    return findings


def _check_conditional_domain(
    workspace: Workspace,
    domain: str,
    filename: str,
    required: bool,
) -> list[PolicyFinding]:
    """Validate one conditional policy file when its domain is required."""
    if not required:
        return []
    return _check_policy_file(workspace, f"{markers.CANONICAL_DIR}{filename}", filename)


def _check_migration(workspace: Workspace) -> list[PolicyFinding]:
    """Emit a finding for every unresolved migration candidate."""
    findings: list[PolicyFinding] = []
    for candidate in evidence.migration_candidates(workspace):
        if candidate.resolved:
            continue
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.ID_MIGRATE}:{candidate.path}",
                path=candidate.path,
                missing_evidence=(
                    f"policy-like content with heading '{candidate.recognized_heading}' "
                    "has not been migrated into the canonical policy directory"
                ),
                required_outcome=(
                    "consolidate the policy into the matching canonical file "
                    f"under {markers.CANONICAL_DIR} and add the "
                    "ralph-workflow-policy:migrated -> marker at this location, "
                    "OR remove the recognized heading so this doc is no longer "
                    "flagged as a candidate"
                ),
            )
        )
    return findings


def validate_readiness(
    workspace: Workspace, stack: ProjectStack
) -> list[PolicyFinding]:
    """Run every deterministic readiness check and return the findings list.

    The function is pure: it does NOT mutate the workspace and it does NOT
    consult any AI. Every check reads through the workspace seam and emits
    zero or more findings. A project is ready iff the returned list is
    empty.
    """
    findings: list[PolicyFinding] = []
    findings.extend(_check_agents_md(workspace))
    findings.extend(_check_claude_md(workspace))
    for filename in markers.CORE_POLICY_FILES:
        findings.extend(_check_core_policy_file(workspace, filename))

    findings.extend(_check_per_language_coverage(workspace, stack))
    findings.extend(_check_verification_bypass(workspace))

    ds_required, _ = evidence.design_system_required(workspace, stack)
    ux_required, _ = evidence.ux_required(workspace, stack)
    perf_required, _ = evidence.performance_required(workspace, stack)
    mem_required, _ = evidence.memory_required(workspace, stack)

    findings.extend(
        _check_conditional_domain(
            workspace, "design-system", markers.CONDITIONAL_POLICY_FILES["design-system"], ds_required
        )
    )
    findings.extend(
        _check_conditional_domain(
            workspace, "ux", markers.CONDITIONAL_POLICY_FILES["ux"], ux_required
        )
    )
    findings.extend(
        _check_conditional_domain(
            workspace,
            "performance",
            markers.CONDITIONAL_POLICY_FILES["performance"],
            perf_required,
        )
    )
    findings.extend(
        _check_conditional_domain(
            workspace,
            "memory-usage",
            markers.CONDITIONAL_POLICY_FILES["memory-usage"],
            mem_required,
        )
    )

    findings.extend(_check_migration(workspace))
    return findings


__all__ = ["validate_readiness"]

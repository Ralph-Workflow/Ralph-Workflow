"""Line-level scanners for the deterministic project-policy validator.

Split out of :mod:`ralph.project_policy.validators` (repo structure policy caps
a module at 1000 lines). This layer owns the ``RALPH-*`` marker regexes and the
pure content extractors the checks are built from: it reads text and returns
values, and never decides readiness on its own.
"""

from __future__ import annotations

import re

from ralph.project_policy import markers
from ralph.project_policy.models import PolicyFinding

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
_REVIEW_LINE_RE = re.compile(r"^RALPH-REVIEW:.*$")
_REVIEW_VALUE_RE = re.compile(r"^RALPH-REVIEW:\s*\S+.*$")
_LANG_LINE_RE = re.compile(r"^RALPH-LANG:\s*(\S.*?)\s*$")
_HEADING_LINE_RE = re.compile(r"^\s*#{1,2}\s+(.+?)\s*$")
_POLICY_ID_LINE_RE = re.compile(r"<!--\s*ralph-policy-id:\s*([^>\s]+)\s*-->")
_PENDING_LINE_RE = re.compile(r"^RALPH-PENDING:.*$")
_PENDING_VALUE_RE = re.compile(r"^RALPH-PENDING:\s*\S+.*$")
_FACT_KEY_VALUE_RE = re.compile(r"^RALPH-FACT:\s*([^:]+):\s*(\S.*?)\s*$")
# A RALPH-PENDING value must carry an ``(assumed <ISO-date>)`` stamp and a
# ``review trigger: <condition>`` clause, so every deferral is visibly
# provisional and names the condition that resurfaces it during normal dev.
_ASSUMED_DATE_RE = re.compile(r"\(assumed \d{4}-\d{2}-\d{2}\)")
_REVIEW_TRIGGER_RE = re.compile(r"review trigger:\s*\S")

# The gates whose presence is satisfied ONLY by a runnable RALPH-COMMAND or a
# RALPH-PENDING deferral — never by RALPH-INAPPLICABLE or RALPH-REVIEW. Testing
# and verification always apply to behavior-bearing software, so they cannot be
# marked "never applies"; they CAN be deferred with RALPH-PENDING (e.g. a new
# project whose test runner is not installed yet), which a dev-cycle agent
# resolves when the trigger fires.
_MANDATORY_GATE_FILES: frozenset[str] = frozenset(
    {"testing-policy.md", "verification-policy.md"}
)

# Per-kind remediation guidance for a malformed gate-form RALPH-PENDING line.
_PENDING_KIND_OUTCOME: dict[str, str] = {
    "unapproved": (
        "start the RALPH-PENDING line with the intended approved gate tool "
        "(e.g. `pytest`, `mypy`, `ruff`, `make <target>`) so the eventual "
        "gate is real"
    ),
    "undated": (
        "add an `(assumed <YYYY-MM-DD>)` stamp with a real date so the "
        "deferral is visibly provisional"
    ),
    "no-trigger": (
        "add a `review trigger: <condition>` clause naming what resolves the "
        "deferral during normal development"
    ),
    "placeholder": (
        "replace the placeholder token in the RALPH-PENDING line with real "
        "values"
    ),
}


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


def _pending_values(content: str) -> list[str]:
    """Return the trimmed values of every *valid* gate-form RALPH-PENDING line.

    A valid pending line has a non-whitespace value (regex
    :data:`_PENDING_VALUE_RE`). Empty pending lines are surfaced separately
    by :func:`_check_individual_pendings` so the user sees a precise finding.
    """
    return [
        line[len(markers.PENDING_MARKER):].strip()
        for line in content.splitlines()
        if _PENDING_VALUE_RE.match(line)
    ]


def _pending_raw_lines(content: str) -> list[str]:
    """Return every gate-form ``RALPH-PENDING:`` line (including empty ones)."""
    return [
        line[len(markers.PENDING_MARKER):].strip()
        for line in content.splitlines()
        if _PENDING_LINE_RE.match(line)
    ]


def _pending_shape_kinds(value: str, *, require_tool: bool) -> list[str]:
    """Return the malformed-shape kinds of a RALPH-PENDING value (empty == ok).

    A placeholder token short-circuits every other check (mirroring the
    command/inapplicable helpers' minimal-findings behavior): the user must
    substitute real values before the date/trigger/tool checks are useful.
    ``require_tool`` is True for the gate form (first token must be an
    approved gate tool) and False for the fact form (no tool token).
    """
    if _has_any(value, markers.PLACEHOLDER_TOKENS) is not None:
        return ["placeholder"]
    kinds: list[str] = []
    if require_tool and not _command_is_approved(value):
        kinds.append("unapproved")
    if _ASSUMED_DATE_RE.search(value) is None:
        kinds.append("undated")
    if _REVIEW_TRIGGER_RE.search(value) is None:
        kinds.append("no-trigger")
    return kinds


def _check_individual_pendings(
    pending_values: list[str], path: str, filename: str
) -> list[PolicyFinding]:
    """Validate every gate-form RALPH-PENDING line.

    RALPH-PENDING is accepted on EVERY policy (including the testing and
    verification gates): a deferral is trusted to be resolved by a dev-cycle
    agent when its review trigger fires, never by re-running remediation. Two
    cases produce a stable ``RWP-PENDING:<file>:<kind>-<n>`` finding:

    * an empty ``RALPH-PENDING:`` line (``empty``);
    * a malformed shape (``unapproved`` / ``undated`` / ``no-trigger`` /
      ``placeholder``).
    """
    findings: list[PolicyFinding] = []
    for index, value in enumerate(pending_values, start=1):
        if not value:
            findings.append(
                PolicyFinding(
                    requirement_id=f"{markers.ID_PENDING}:{filename}:empty-{index}",
                    path=path,
                    missing_evidence=f"RALPH-PENDING line {index} is empty",
                    required_outcome=(
                        "declare the deferred gate as `RALPH-PENDING: "
                        "<approved-tool> (assumed <YYYY-MM-DD>); review "
                        "trigger: <condition>` or remove the line"
                    ),
                )
            )
            continue
        findings.extend(
            PolicyFinding(
                requirement_id=f"{markers.ID_PENDING}:{filename}:{kind}-{index}",
                path=path,
                missing_evidence=(
                    f"RALPH-PENDING line {index} is malformed ({kind})"
                ),
                required_outcome=_PENDING_KIND_OUTCOME[kind],
            )
            for kind in _pending_shape_kinds(value, require_tool=True)
        )
    return findings


def _check_pending_facts(
    content: str, path: str, filename: str
) -> list[PolicyFinding]:
    """Validate every fact-form RALPH-PENDING value's shape.

    A ``RALPH-FACT`` whose value leads with the RALPH-PENDING sentinel is a
    deferred fact. The placeholder kind is intentionally NOT re-reported here
    (the per-fact-line placeholder scan in :func:`_check_placeholders` already
    owns it); this helper adds the ``undated`` / ``no-trigger`` shape findings
    specific to the deferral form.
    """
    findings: list[PolicyFinding] = []
    index = 0
    for line in content.splitlines():
        match = _FACT_KEY_VALUE_RE.match(line)
        if match is None:
            continue
        value = str(match.group(2)).strip()
        if not value.startswith(markers.PENDING_SENTINEL):
            continue
        index += 1
        for kind in _pending_shape_kinds(value, require_tool=False):
            if kind == "placeholder":
                continue
            findings.append(
                PolicyFinding(
                    requirement_id=(
                        f"{markers.ID_PENDING}:{filename}:fact-{kind}-{index}"
                    ),
                    path=path,
                    missing_evidence=(
                        f"deferred RALPH-FACT (RALPH-PENDING) {index} is "
                        f"malformed ({kind})"
                    ),
                    required_outcome=_PENDING_KIND_OUTCOME[kind],
                )
            )
    return findings


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


def _lang_blocks(content: str) -> dict[str, tuple[bool, bool, bool]]:
    """Return language name -> (has_command, has_inapplicable, has_pending).

    The validator slices the file into per-language blocks delimited by
    ``RALPH-LANG:`` lines. Each block ends at the next RALPH-LANG line OR
    the end of the file. A block must contain at least one non-empty
    RALPH-COMMAND, RALPH-INAPPLICABLE, or RALPH-PENDING line to satisfy
    per-language coverage — empty marker lines are NOT counted as a
    declaration. Gate-form RALPH-PENDING shape is validated at file scope by
    :func:`_check_individual_pendings`; this helper only counts presence.
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
    out: dict[str, tuple[bool, bool, bool]] = {}
    for lang, lines in blocks.items():
        joined = "\n".join(lines)
        out[lang] = (
            bool(_command_values(joined)),
            bool(_inapplicable_lines_for_lang_block(lines)),
            bool(_pending_values(joined)),
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
    tokens = stripped.split()
    for token in tokens:
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*=.*", token):
            continue
        return token
    return ""


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
    return first in markers.APPROVED_GATE_TOOLS or first.startswith(("./", "bin/"))

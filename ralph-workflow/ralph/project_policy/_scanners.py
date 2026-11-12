from __future__ import annotations

import re
from datetime import date

from ralph.project_policy import markers
from ralph.project_policy.models import PolicyFinding

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
_ASSUMED_DATE_RE = re.compile(r"\(assumed (?P<date>\d{4}-\d{2}-\d{2})\)")
_REVIEW_TRIGGER_RE = re.compile(r"review trigger:\s*\S")

_MANDATORY_GATE_FILES: frozenset[str] = frozenset({"testing-policy.md", "verification-policy.md"})

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
    "invalid-date": (
        "replace the `(assumed <YYYY-MM-DD>)` stamp with a real calendar "
        "date so the deferral is truthfully dated"
    ),
    "invalid-sentinel": (
        "start the deferred fact value with the exact `RALPH-PENDING` token followed by whitespace"
    ),
    "no-trigger": (
        "add a `review trigger: <condition>` clause naming what resolves the "
        "deferral during normal development"
    ),
    "placeholder": ("replace the placeholder token in the RALPH-PENDING line with real values"),
}


def _has_any(content: str, substrings: tuple[str, ...]) -> str | None:
    for sub in substrings:
        if sub in content:
            return sub
    return None


def _contains_placeholder(content: str) -> str | None:
    return _has_any(content, markers.PLACEHOLDER_TOKENS)


def _command_values(content: str) -> list[str]:
    marker = markers.COMMAND_MARKER
    values: list[str] = []
    for line in content.splitlines():
        if line.startswith(marker):
            match = _COMMAND_VALUE_RE.match(line)
            if match is not None:
                value = line[len(marker) :].strip()
                values.append(value)
    return values


def _command_raw_lines(content: str) -> list[str]:
    return [
        line[len(markers.COMMAND_MARKER) :].strip()
        for line in content.splitlines()
        if _COMMAND_LINE_RE.match(line)
    ]


def _inapplicable_lines(content: str) -> list[str]:
    return [
        line[len(markers.INAPPLICABLE_MARKER) :].strip()
        for line in content.splitlines()
        if _INAPPLICABLE_VALUE_RE.match(line)
    ]


def _inapplicable_raw_lines(content: str) -> list[str]:
    return [
        line[len(markers.INAPPLICABLE_MARKER) :].strip()
        for line in content.splitlines()
        if _INAPPLICABLE_LINE_RE.match(line)
    ]


def _inapplicable_present(content: str) -> bool:
    return bool(_inapplicable_lines(content))


def _pending_values(content: str) -> list[str]:
    return [
        line[len(markers.PENDING_MARKER) :].strip()
        for line in content.splitlines()
        if _PENDING_VALUE_RE.match(line)
    ]


def _pending_raw_lines(content: str) -> list[str]:
    return [
        line[len(markers.PENDING_MARKER) :].strip()
        for line in content.splitlines()
        if _PENDING_LINE_RE.match(line)
    ]


def _pending_shape_kinds(value: str, *, require_tool: bool) -> list[str]:
    if _has_any(value, markers.PLACEHOLDER_TOKENS) is not None:
        return ["placeholder"]
    kinds: list[str] = []
    if require_tool and not _command_is_approved(value):
        kinds.append("unapproved")
    assumed_date = _ASSUMED_DATE_RE.search(value)
    if assumed_date is None:
        kinds.append("undated")
    else:
        try:
            date.fromisoformat(assumed_date.group("date"))
        except ValueError:
            kinds.append("invalid-date")
    if _REVIEW_TRIGGER_RE.search(value) is None:
        kinds.append("no-trigger")
    return kinds


def _check_individual_pendings(
    pending_values: list[str], path: str, filename: str
) -> list[PolicyFinding]:
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
                missing_evidence=(f"RALPH-PENDING line {index} is malformed ({kind})"),
                required_outcome=_PENDING_KIND_OUTCOME[kind],
            )
            for kind in _pending_shape_kinds(value, require_tool=True)
        )
    return findings


def _check_pending_facts(content: str, path: str, filename: str) -> list[PolicyFinding]:
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
        sentinel_suffix = value[len(markers.PENDING_SENTINEL) :]
        if sentinel_suffix and not sentinel_suffix[0].isspace():
            findings.append(
                PolicyFinding(
                    requirement_id=(
                        f"{markers.ID_PENDING}:{filename}:fact-invalid-sentinel-{index}"
                    ),
                    path=path,
                    missing_evidence=(
                        f"deferred RALPH-FACT {index} uses a near-miss RALPH-PENDING sentinel"
                    ),
                    required_outcome=_PENDING_KIND_OUTCOME["invalid-sentinel"],
                )
            )
            continue
        for kind in _pending_shape_kinds(value, require_tool=False):
            if kind == "placeholder":
                continue
            findings.append(
                PolicyFinding(
                    requirement_id=(f"{markers.ID_PENDING}:{filename}:fact-{kind}-{index}"),
                    path=path,
                    missing_evidence=(
                        f"deferred RALPH-FACT (RALPH-PENDING) {index} is malformed ({kind})"
                    ),
                    required_outcome=_PENDING_KIND_OUTCOME[kind],
                )
            )
    return findings


def _inapplicable_lines_for_lang_block(block_lines: list[str]) -> list[str]:
    joined = "\n".join(block_lines)
    return _inapplicable_lines(joined)


def _fact_lines(content: str) -> list[str]:
    out: list[str] = []
    for line in content.splitlines():
        if not _FACT_VALUE_RE.match(line):
            continue
        out.append(line[len(markers.FACT_MARKER) :].strip())
    return out


def _lang_blocks(content: str) -> dict[str, tuple[bool, bool, bool]]:
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
    headings: set[str] = set()
    for line in content.splitlines():
        match = _HEADING_LINE_RE.match(line)
        if match is not None:
            text = str(match.group(1)).strip().lower()
            headings.add(text)
    return headings


def _citation_blocks(content: str) -> list[str]:
    lines = content.splitlines()
    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip().lower() == "## research basis":
            start_idx = idx + 1
            break
    if start_idx is None:
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
    first = _command_first_token(value)
    if not first:
        return False
    return first in markers.APPROVED_GATE_TOOLS

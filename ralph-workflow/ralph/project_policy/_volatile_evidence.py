from __future__ import annotations

import re
from typing import Final

from ralph.project_policy import markers
from ralph.project_policy._findings import PolicyFinding

_PATH_CHARS: Final[str] = r"[^\s`'\"(),;:]"

_LOCAL_DIR_PATH: Final[str] = rf"(?:tmp|\.agent)/{_PATH_CHARS}+"

_LEDGER_FILE_PATH: Final[str] = (
    rf"(?!(?:tests?|spec)/)(?!{_PATH_CHARS}*/fixtures/)"
    rf"{_PATH_CHARS}+\.(?:ndjson|jsonl|log)"
)

_LINE_CITATION_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?<![\w/.:-])`?(?P<path>{_LOCAL_DIR_PATH}|{_LEDGER_FILE_PATH})`?"
    r"(?::\s*|#L)(?P<line>\d+)\b"
)

_RUN_RECEIPT_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?<![\w/.:-])`?(?P<path>(?:tmp|\.agent)/{_PATH_CHARS}*"
    r"(?:\d{8}T\d{4,}|\d{6,}|[0-9a-f]{12,})"
    rf"{_PATH_CHARS}*|\.agent/{_PATH_CHARS}+\.(?:ndjson|jsonl|log))`?"
)

_VOLATILE_RECORD_RE: Final[re.Pattern[str]] = re.compile(
    rf"(?<![\w/.:-])`?(?:{_LOCAL_DIR_PATH}|{_PATH_CHARS}+\.(?:ndjson|jsonl))`?"
)

_RECORD_COUNT_RE: Final[re.Pattern[str]] = re.compile(
    r"\b\d[\d,]*\s+(?:[\w-]+\s+){0,3}(?:rows?|lines?|entries|records)\b"
)

_SHOWN_CITATIONS: Final[int] = 3

_REQUIRED_OUTCOME: Final[str] = (
    "replace every citation of worktree-local or append-only evidence (a line, row count, "
    "excerpt, or per-run receipt under `tmp/` or `.agent/`, or in an `*.ndjson`/`*.jsonl`/"
    "`*.log` ledger) with durable text: name the stage or profile and record its last run "
    "as a calendar date with a durable source (a CI run id, a committed baseline, or the "
    "gate-lane review entry), and derive any verdict from the local ledger at run time "
    "instead of committing it. Those records differ in every checkout and go stale on the "
    "next run, so never re-point or recount a citation; a check whose oracle compares "
    "committed text with a worktree-local record tests no product behaviour and is replaced "
    "by the run-time derivation (Ralph's bundled verification starter, High-churn evidence)"
)


def _citations(content: str) -> list[str]:
    spans: list[tuple[int, int, str]] = []
    for pattern in (_LINE_CITATION_RE, _RUN_RECEIPT_RE):
        for match in pattern.finditer(content):
            if any(start <= match.start() < end for start, end, _ in spans):
                continue
            spans.append((match.start(), match.end(), match.group(0).strip("`")))
    for line in content.splitlines():
        record = _VOLATILE_RECORD_RE.search(line)
        count = _RECORD_COUNT_RE.search(line)
        if record is None or count is None:
            continue
        if any(record.group(0).strip("`") == text for _, _, text in spans):
            continue
        spans.append(
            (len(content), len(content), f"{count.group(0)} of {record.group(0).strip('`')}")
        )
    return [text for _, _, text in sorted(spans)]


def check_volatile_evidence_citations(
    content: str, path: str, filename: str
) -> list[PolicyFinding]:
    citations = _citations(content)
    if not citations:
        return []
    shown = ", ".join(citations[:_SHOWN_CITATIONS])
    more = len(citations) - _SHOWN_CITATIONS
    suffix = f", and {more} more" if more > 0 else ""
    return [
        PolicyFinding(
            requirement_id=f"{markers.ID_VOLATILE_EVIDENCE}:{filename}",
            path=path,
            missing_evidence=(
                f"{len(citations)} citation(s) into worktree-local or append-only evidence"
            ),
            required_outcome=f"{_REQUIRED_OUTCOME}; cited here: {shown}{suffix}",
        )
    ]


_MAX_REPORTED_DIFF_CITATIONS: Final[int] = 20


def volatile_citations_in_added_lines(diff_text: str) -> list[str]:
    found: list[str] = []
    current_file = ""
    for line in diff_text.splitlines():
        if line.startswith("+++ "):
            target = line[4:].strip()
            current_file = target[2:] if target.startswith("b/") else target
            continue
        if not line.startswith("+") or not current_file or current_file == "/dev/null":
            continue
        found.extend(f"{current_file}: {citation}" for citation in _citations(line[1:]))
        if len(found) >= _MAX_REPORTED_DIFF_CITATIONS:
            return found[:_MAX_REPORTED_DIFF_CITATIONS]
    return found

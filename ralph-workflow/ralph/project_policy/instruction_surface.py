from __future__ import annotations

import re
from typing import Final

from ralph.project_policy import markers
from ralph.project_policy.models import PolicyFinding

HIDDEN_UNICODE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[\u061c\u200b-\u200f\u202a-\u202e\u2060\u2066-\u2069\ufeff\u00ad]"
)


def find_hidden_unicode(text: str) -> list[tuple[int, int, str]]:
    results: list[tuple[int, int, str]] = []
    for line_idx, line in enumerate(text.splitlines(), start=1):
        for match in HIDDEN_UNICODE_PATTERN.finditer(line):
            char = match.group(0)
            col = match.start() + 1
            code_hex = f"U+{ord(char):04X}"
            results.append((line_idx, col, code_hex))
    return results


def check_hidden_unicode(content: str, path: str) -> list[PolicyFinding]:
    occurrences = find_hidden_unicode(content)
    if not occurrences:
        return []
    findings: list[PolicyFinding] = []
    for line_idx, col, code_hex in occurrences:
        findings.append(
            PolicyFinding(
                requirement_id=f"{markers.HIDDEN_UNICODE_FINDING_ID}:line-{line_idx}",
                path=path,
                missing_evidence=(
                    f"file contains hidden/bidi Unicode character {code_hex} "
                    f"at line {line_idx}, column {col}"
                ),
                required_outcome="remove all zero-width and bidi control characters",
            )
        )
    return findings


__all__ = [
    "HIDDEN_UNICODE_PATTERN",
    "check_hidden_unicode",
    "find_hidden_unicode",
]

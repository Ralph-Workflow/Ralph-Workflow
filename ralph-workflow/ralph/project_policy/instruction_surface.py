"""Instruction-surface integrity checks: hidden-character lint and helpers.

This module detects zero-width spaces, directional formatting overrides, and
other visually invisible or deceptive Unicode characters in instruction surface
files (AGENTS.md, policy files, prompt templates, and MCP configs).
"""

from __future__ import annotations

import re
from typing import Final

from ralph.project_policy import markers
from ralph.project_policy.models import PolicyFinding

# Zero-width and bidirectional control characters that pose security,
# prompt-injection, or invisible-instruction risks:
# U+200B..U+200F: Zero-width space, ZWNJ, ZWJ, LRM, RLM
# U+202A..U+202E: LRE, RLE, PDF, LRO, RLO (bidi embedding/override)
# U+2066..U+2069: LRI, RLI, FSI, PDI (bidi isolate controls)
# U+FEFF: Byte order mark / zero-width no-break space
# U+00AD: Soft hyphen
HIDDEN_UNICODE_PATTERN: Final[re.Pattern[str]] = re.compile(
    r"[\u200b-\u200f\u202a-\u202e\u2066-\u2069\ufeff\u00ad]"
)


def find_hidden_unicode(text: str) -> list[tuple[int, int, str]]:
    """Scan *text* and return ``(line_number, column, hex_codepoint)`` for each hidden character.

    Line and column numbers are 1-indexed.
    """
    results: list[tuple[int, int, str]] = []
    for line_idx, line in enumerate(text.splitlines(), start=1):
        for match in HIDDEN_UNICODE_PATTERN.finditer(line):
            char = match.group(0)
            col = match.start() + 1
            code_hex = f"U+{ord(char):04X}"
            results.append((line_idx, col, code_hex))
    return results


def check_hidden_unicode(content: str, path: str) -> list[PolicyFinding]:
    """Validate that *content* contains no zero-width or bidi Unicode characters.

    Returns deterministic :class:`PolicyFinding` instances for any occurrences.
    """
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

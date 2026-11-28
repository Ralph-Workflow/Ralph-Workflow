from __future__ import annotations

import re
from typing import Final

_FILE_HEADER: Final[str] = "+++ "

_RUN_TIMESTAMP_RE: Final[re.Pattern[str]] = re.compile(
    r"(?<![\w-])\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d+)?)?(?:Z|[+-]\d{2}:?\d{2})?(?![\w:.])"
)


def run_timestamps_in_added_lines(diff: str) -> tuple[str, ...]:
    path = ""
    removed: set[tuple[str, str]] = set()
    added: list[tuple[str, str]] = []
    for line in diff.splitlines():
        if line.startswith(_FILE_HEADER):
            target = line[len(_FILE_HEADER) :].strip()
            path = target[2:] if target.startswith("b/") else target
            continue
        if line.startswith(("+++", "---")) or line[:1] not in {"+", "-"}:
            continue
        stamps = ((path, match.group(0)) for match in _RUN_TIMESTAMP_RE.finditer(line[1:]))
        if line.startswith("-"):
            removed.update(stamps)
        else:
            added.extend(stamps)
    findings: list[str] = []
    for entry_path, stamp in added:
        finding = f"{entry_path}: run timestamp {stamp}"
        if (entry_path, stamp) not in removed and finding not in findings:
            findings.append(finding)
    return tuple(findings)


__all__ = ["run_timestamps_in_added_lines"]

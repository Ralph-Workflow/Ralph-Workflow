"""Guard-inventory parser and claim-scope validator (v5, B7 / B9).

The v5 plan introduces a per-starter ``## Guard inventory`` markdown
table whose header and column grammar are pinned in
:mod:`ralph.project_policy.markers`. The parser lives in its own
module to keep :mod:`ralph.project_policy._content_checks` under
the repository's 1000-line cap. The parser exposes a small,
deterministic API:

* :func:`_parse_guard_inventory` returns the data rows of one
  starter's table (or ``[]`` when the section is missing or
  malformed).
* :func:`_check_guard_inventory` runs the validator check and
  returns a list of :class:`PolicyFinding` objects. The check
  emits one finding for a missing section, one for a malformed
  table, and per-row findings for empty cells and broad-guarantee
  phrases (the claim-scope rule, B7).
"""

from __future__ import annotations

import re

from ralph.project_policy import markers
from ralph.project_policy.models import PolicyFinding

_GUARD_INVENTORY_HEADER_RE = re.compile(r"^\|\s*([A-Za-z ]+?)\s*\|$")


def _parse_guard_inventory(content: str) -> list[dict[str, str]]:
    """Parse the ``## Guard inventory`` table from a starter / project policy.

    Returns one dict per data row, with the five canonical keys
    (``guard``, ``owner``, ``protected_outcome``, ``detected_condition``,
    ``retirement_trigger``) lowercased to canonical form. The header
    row is matched case-insensitively against
    :data:`markers.GUARD_INVENTORY_HEADER`; the leading and trailing
    pipe are stripped and any escape sequence (``\\|``) is unescaped.
    The function returns an empty list when the section is missing,
    when the table has no header, or when the header doesn't match —
    the caller emits a single RWP-INVENTORY:* finding rather than
    per-row findings in those cases, because the whole table is
    broken and partial findings would mislead remediation.
    """
    lines = content.splitlines()
    try:
        start = lines.index(markers.GUARD_INVENTORY_HEADING)
    except ValueError:
        return []
    # Collect every consecutive line that looks like a table row
    # (starts with ``|``); the first such line is the header, the next
    # is a markdown table separator (which we skip), and the rest are
    # data rows. We stop as soon as we leave the table (a non-``|`` line
    # after at least one data row has been collected).
    rows: list[list[str]] = []
    saw_data = False
    for line in lines[start + 1 :]:
        if not line.lstrip().startswith("|"):
            if saw_data:
                break  # left the table
            continue
        if _is_table_separator(line):
            continue  # skip the separator row
        cells = _split_table_row(line)
        rows.append(cells)
        saw_data = True
    if not rows:
        return []
    # First row is the header; the canonical headers must match.
    header_cells = [c.strip().lower() for c in rows[0]]
    canonical = [h.lower() for h in markers.GUARD_INVENTORY_HEADER]
    if header_cells != canonical:
        return []
    out: list[dict[str, str]] = []
    for cells in rows[1:]:
        if len(cells) != len(canonical):
            return []  # malformed (off-by-one in cells); treat whole table as broken
        # Unescape backslash-pipe in cells.
        unescaped = [c.replace("\\|", "|") for c in cells]
        out.append(
            {
                "guard": unescaped[0].strip(),
                "owner": unescaped[1].strip(),
                "protected_outcome": unescaped[2].strip(),
                "detected_condition": unescaped[3].strip(),
                "retirement_trigger": unescaped[4].strip(),
            }
        )
    return out


def _is_table_separator(line: str) -> bool:
    """Return True when ``line`` is a markdown table separator (a row of
    pipes, dashes, and optional colons)."""
    stripped = line.strip()
    if not stripped.startswith("|") or not stripped.endswith("|"):
        return False
    inner = stripped[1:-1]
    for raw_cell in inner.split("|"):
        stripped_cell = raw_cell.strip()
        if not stripped_cell:
            return False
        if not re.fullmatch(r":?-+:?|\s*", stripped_cell):
            return False
    return True


def _split_table_row(line: str) -> list[str]:
    """Split a markdown table row into cells, preserving escaped pipes.

    A leading and trailing pipe are stripped; backslash-pipe is
    preserved as a literal pipe in the cell value. Empty cells
    (``||``) become empty strings; whitespace-only cells are returned
    as-is so the caller can flag the empty-cell contract violation
    separately from the table-shape violation.
    """
    stripped = line.strip()
    if stripped.startswith("|"):
        stripped = stripped[1:]
    if stripped.endswith("|"):
        stripped = stripped[:-1]
    cells: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(stripped):
        ch = stripped[i]
        if ch == "\\" and i + 1 < len(stripped) and stripped[i + 1] == "|":
            current.append("|")
            i += 2
            continue
        if ch == "|":
            cells.append("".join(current))
            current = []
            i += 1
            continue
        current.append(ch)
        i += 1
    cells.append("".join(current))
    return cells


def _starts_with_heading(content: str, heading: str) -> bool:
    """Return True if ``content`` carries the ``## Heading`` line."""
    return any(line.strip() == heading for line in content.splitlines())


def _claim_scope_findings(
    filename: str,
    path: str,
    index: int,
    outcome: str,
    detected: str,
) -> list[PolicyFinding]:
    """Return per-phrase claim-scope findings for one row.

    Extracted from :func:`_check_guard_inventory` to keep the per-row
    branch readable; a broad phrase that does not also appear in the
    detected condition is rejected with a stable RWP-INVENTORY finding.
    """
    findings: list[PolicyFinding] = []
    for phrase in markers.CLAIM_SCOPE_PHRASES:
        if phrase not in outcome:
            continue
        if phrase in detected:
            continue
        findings.append(
            PolicyFinding(
                requirement_id=(f"{markers.ID_INVENTORY}:{filename}:row-{index}:claim-scope"),
                path=path,
                missing_evidence=(
                    f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                    f"protected_outcome carries broad phrase "
                    f"{phrase!r} that does not appear in detected_condition; "
                    "rewrite the outcome to name only what the detector observes"
                ),
                required_outcome=(
                    f"either narrow row {index} protected_outcome to a "
                    "claim about the detector's actual observation, or "
                    f"add the phrase {phrase!r} to detected_condition"
                ),
            )
        )
    return findings


def _check_guard_inventory(content: str, path: str, filename: str) -> list[PolicyFinding]:
    """Validate the ``## Guard inventory`` table per v5 contract (B9).

    Findings emitted:

    * One finding when the section is missing entirely.
    * One finding when the header row is malformed (does not match
      :data:`markers.GUARD_INVENTORY_HEADER`).
    * One finding per guard-inventory row whose ``Protected outcome``
      cell carries a broad-guarantee phrase from
      :data:`markers.CLAIM_SCOPE_PHRASES` that does not also appear in
      the row's ``Detected condition`` (the deterministic claim-scope
      rule, B7).
    * One finding per row with an empty cell in any of the five
      required columns.

    The check returns an empty list when the table is well-formed:
    no findings means the starter is B9-compliant.
    """
    if not _starts_with_heading(content, markers.GUARD_INVENTORY_HEADING):
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_INVENTORY}:{filename}:missing",
                path=path,
                missing_evidence=(
                    f"file lacks a {markers.GUARD_INVENTORY_HEADING} section; "
                    "every applicable starter must enumerate its guards with "
                    "the pinned five-cell row"
                ),
                required_outcome=(
                    f"add a `{markers.GUARD_INVENTORY_HEADING}` section "
                    "with the pinned header row and one data row per guard "
                    "(Guard | Owner | Protected outcome | Detected "
                    "condition | Retirement trigger)"
                ),
            )
        ]
    rows = _parse_guard_inventory(content)
    if not rows:
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_INVENTORY}:{filename}:malformed",
                path=path,
                missing_evidence=(
                    f"{markers.GUARD_INVENTORY_HEADING} section is present "
                    "but the table is empty or its header row does not "
                    "match the pinned grammar"
                ),
                required_outcome=(
                    f"fix the table to match the pinned header "
                    f"({' | '.join(markers.GUARD_INVENTORY_HEADER)}) and "
                    "include at least one data row"
                ),
            )
        ]
    findings: list[PolicyFinding] = []
    for index, row in enumerate(rows, start=1):
        for column in (
            "guard",
            "owner",
            "protected_outcome",
            "detected_condition",
            "retirement_trigger",
        ):
            if row[column]:
                continue
            findings.append(
                PolicyFinding(
                    requirement_id=(
                        f"{markers.ID_INVENTORY}:{filename}:row-{index}:empty-{column}"
                    ),
                    path=path,
                    missing_evidence=(
                        f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                        f"has an empty {column!r} cell"
                    ),
                    required_outcome=(
                        f"fill the {column!r} cell of row {index} with a "
                        "non-empty value (use `none` only when the "
                        "column genuinely has no value to record)"
                    ),
                )
            )
        # Apply the deterministic claim-scope rule to each row.
        outcome = row["protected_outcome"].lower()
        detected = row["detected_condition"].lower()
        findings.extend(_claim_scope_findings(filename, path, index, outcome, detected))
    return findings


__all__ = [
    "_check_guard_inventory",
    "_parse_guard_inventory",
]

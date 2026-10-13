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
from typing import Final

from ralph.project_policy import markers
from ralph.project_policy.models import PolicyFinding

_MIN_TABLE_ROW_LENGTH = 2
_MIN_TABLE_LINES = 3


def _split_table_row(line: str) -> list[str] | None:
    """Split a markdown table row into cells, requiring balanced pipes.

    A leading pipe and unescaped trailing pipe are strictly required.
    Backslash-pipe (``\\|``) is preserved as a literal pipe in the cell value.
    If the row lacks a leading pipe or a trailing unescaped pipe, returns
    ``None``. Empty cells (``||``) become empty strings; whitespace-only
    cells are returned as whitespace strings so caller can flag empty-cell
    violations.
    """
    stripped = line.strip()
    if (
        len(stripped) < _MIN_TABLE_ROW_LENGTH
        or not stripped.startswith("|")
        or not stripped.endswith("|")
    ):
        return None
    # Check that trailing pipe is not escaped by an odd number of backslashes.
    num_backslashes = 0
    k = len(stripped) - 2
    while k >= 0 and stripped[k] == "\\":
        num_backslashes += 1
        k -= 1
    if num_backslashes % 2 == 1:
        return None

    inner = stripped[1:-1]
    cells: list[str] = []
    current: list[str] = []
    i = 0
    while i < len(inner):
        ch = inner[i]
        if ch == "\\":
            if i + 1 < len(inner):
                next_ch = inner[i + 1]
                if next_ch == "\\":
                    current.append("\\")
                    i += 2
                    continue
                if next_ch == "|":
                    current.append("|")
                    i += 2
                    continue
            current.append(ch)
            i += 1
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


def _is_table_separator(line: str, expected_columns: int = 5) -> bool:
    """Return True when ``line`` is a markdown table separator (a row of
    pipes, dashes, and optional colons) with exactly ``expected_columns`` cells."""
    stripped = line.strip()
    if (
        len(stripped) < _MIN_TABLE_ROW_LENGTH
        or not stripped.startswith("|")
        or not stripped.endswith("|")
    ):
        return False
    num_backslashes = 0
    k = len(stripped) - 2
    while k >= 0 and stripped[k] == "\\":
        num_backslashes += 1
        k -= 1
    if num_backslashes % 2 == 1:
        return False
    inner = stripped[1:-1]
    cells = inner.split("|")
    if len(cells) != expected_columns:
        return False
    for raw_cell in cells:
        stripped_cell = raw_cell.strip()
        if not stripped_cell:
            return False
        if not re.fullmatch(r":?-+:?", stripped_cell):
            return False
    return True


def _extract_table_lines(content: str) -> list[str] | None:
    """Extract consecutive non-empty lines from the guard inventory section."""
    lines = content.splitlines()
    start = -1
    for idx, line in enumerate(lines):
        if line.strip() == markers.GUARD_INVENTORY_HEADING:
            start = idx
            break
    if start == -1:
        return None

    section_lines: list[str] = []
    for line in lines[start + 1 :]:
        if line.lstrip().startswith("#"):
            break
        section_lines.append(line)

    table_lines = [line.strip() for line in section_lines if line.strip()]
    if len(table_lines) < _MIN_TABLE_LINES:
        return None
    return table_lines


def _parse_guard_inventory(content: str) -> list[dict[str, str]]:
    """Parse the ``## Guard inventory`` table from a starter / project policy.

    Returns one dict per data row, with the five canonical keys
    (``guard``, ``owner``, ``protected_outcome``, ``detected_condition``,
    ``retirement_trigger``) lowercased to canonical form. The header
    row is matched case-insensitively against
    :data:`markers.GUARD_INVENTORY_HEADER`; leading and trailing pipes
    are required, and pipes must be balanced.
    The function returns an empty list when the section is missing,
    when the table has no header, when the header doesn't match,
    when separators or trailing/leading pipes are missing, or when
    any row is malformed — the caller emits a single RWP-INVENTORY:*
    finding in those cases so malformed tables fail closed.
    """
    table_lines = _extract_table_lines(content)
    if table_lines is None:
        return []

    canonical = [h.lower() for h in markers.GUARD_INVENTORY_HEADER]

    # 1. Header row & 2. Separator row
    header_cells = _split_table_row(table_lines[0])
    valid_header = (
        header_cells is not None
        and len(header_cells) == len(canonical)
        and [c.strip().lower() for c in header_cells] == canonical
    )
    valid_sep = _is_table_separator(table_lines[1], expected_columns=len(canonical))
    if not (valid_header and valid_sep):
        return []

    # 3. Data rows: each must have leading/trailing pipe and balanced 6 columns
    out: list[dict[str, str]] = []
    for line in table_lines[2:]:
        cells = _split_table_row(line)
        if cells is None or len(cells) != len(canonical):
            return []  # malformed row; fail closed for whole table
        out.append(
            {
                "guard": cells[0].strip(),
                "owner": cells[1].strip(),
                "protected_outcome": cells[2].strip(),
                "detected_condition": cells[3].strip(),
                "retirement_trigger": cells[4].strip(),
                "claim_scope": cells[5].strip(),
            }
        )
    return out


def _starts_with_heading(content: str, heading: str) -> bool:
    """Return True if ``content`` carries the ``## Heading`` line."""
    return any(line.strip() == heading for line in content.splitlines())


# Sections that are excluded from the ratchet-linkage scan (the rule is
# only about declarations in the BODY prose, not the inventory table
# itself or research/citation blocks that legitimately use the
# ``ratcheted`` / ``ratchet`` / ``MUST NOT grow`` / ``MUST NOT increase``
# phrases when describing prior decisions or sources).
_EXCLUDED_SECTION_HEADINGS: Final[tuple[str, ...]] = (
    "## Guard inventory",
    "## Maintenance triggers",
    "## Research basis",
    "## Portfolio ownership",
    "## Living document contract",
    "## Ralph markers",
)


def _ratchet_declarations(content: str) -> list[tuple[str, str]]:
    """Return ``(declaration_text, control_id_or_empty)`` per body ratchet.

    A body ratchet declaration is a *paragraph* in the starter body
    (excluding the Guard inventory table and the
    :data:`_EXCLUDED_SECTION_HEADINGS` sections) that contains any
    phrase from :data:`markers.RATCHET_DECLARATION_PHRASES`
    (case-insensitive). A paragraph is one or more consecutive
    non-blank, non-heading lines (markdown hard-break semantics). The
    function returns the joined paragraph text and the captured
    control id when an inline ``<!-- ralph-control: <id> -->`` token
    appears anywhere in that paragraph; an empty string when the
    token is absent.

    The grammar is regex-deterministic and excludes the inventory
    table; :func:`_check_guard_inventory` calls this and emits
    ``RWP-INVENTORY:RATCHET-UNCOVERED`` for each declaration that has
    no control id.
    """
    out: list[tuple[str, str]] = []
    in_excluded = False
    in_table = False
    control_re = re.compile(markers.CONTROL_TOKEN_RE)
    phrase_alts = "|".join(re.escape(p) for p in markers.RATCHET_DECLARATION_PHRASES)
    # The phrases are not all word-shaped (``**Ratchet:**`` ends in
    # non-word chars) so we use a plain case-insensitive substring
    # match rather than the usual ``\b...\b`` word-boundary form.
    phrase_re = re.compile(rf"(?i)(?:{phrase_alts})")
    paragraph: list[str] = []
    paragraphs: list[str] = []
    for raw_line in content.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("#"):
            in_excluded = stripped in _EXCLUDED_SECTION_HEADINGS
            in_table = stripped == markers.GUARD_INVENTORY_HEADING
            if paragraph:
                paragraphs.append(" ".join(paragraph))
                paragraph = []
            continue
        if in_excluded or in_table:
            if paragraph:
                paragraphs.append(" ".join(paragraph))
                paragraph = []
            continue
        if not stripped:
            if paragraph:
                paragraphs.append(" ".join(paragraph))
                paragraph = []
            continue
        paragraph.append(stripped)
    if paragraph:
        paragraphs.append(" ".join(paragraph))
    for p in paragraphs:
        if not phrase_re.search(p):
            continue
        match = control_re.search(p)
        out.append((p, match.group(1) if match else ""))
    return out


def _control_id_for_guard_row(guard_cell: str) -> str | None:
    """Return the leading control id from a Guard cell, or ``None``.

    The grammar allows a row's Guard cell to begin with a backticked
    id `` `<id>` `` (any text may follow). The function captures the
    first such id using :data:`markers.CONTROL_ID_RE`. Returns
    ``None`` when the cell does not begin with a backticked id so
    callers can distinguish a row with no linkage from a row whose
    control id is the empty string.
    """
    match = re.match(markers.CONTROL_ID_RE, guard_cell.strip())
    return match.group(1) if match else None


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
    * One finding per row with an empty cell in any of the six
      required columns (the sixth is ``Claim scope``).
    * One ``RWP-INVENTORY:<file>:ratchet-uncovered`` finding per body
      ratchet declaration that has no matching control id, and one
      per body ratchet whose control id does not appear at the start
      of any Guard cell.
    * One ``RWP-INVENTORY:<file>:control-orphan`` finding per Guard
      cell control id that no body declaration names.
    * One finding per row whose ``Claim scope`` cell carries a
      broad-guarantee phrase from :data:`markers.CLAIM_SCOPE_PHRASES`
      that does NOT also appear in the same row's ``Detected
      condition`` cell (the predicate is applied to the new
      ``Claim scope`` field too).

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
                    "the pinned six-cell row"
                ),
                required_outcome=(
                    f"add a `{markers.GUARD_INVENTORY_HEADING}` section "
                    "with the pinned header row and one data row per guard "
                    "(Guard | Owner | Protected outcome | Detected "
                    "condition | Retirement trigger | Claim scope)"
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
    row_control_ids: set[str] = set()
    for index, row in enumerate(rows, start=1):
        for column in (
            "guard",
            "owner",
            "protected_outcome",
            "detected_condition",
            "retirement_trigger",
            "claim_scope",
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
        # Apply the deterministic claim-scope rule to each row's
        # ``Protected outcome`` AND ``Claim scope`` cells. The same
        # predicate fires on a broad phrase absent from the row's
        # ``Detected condition`` for both cells.
        outcome = row["protected_outcome"].lower()
        scope = row["claim_scope"].lower()
        detected = row["detected_condition"].lower()
        findings.extend(_claim_scope_findings(filename, path, index, outcome, detected))
        findings.extend(_claim_scope_findings(filename, path, index, scope, detected))
        # Track the row's leading control id, if any, for the
        # orphan / uncovered checks below. A row without a leading
        # backticked id does NOT participate in the linkage scan.
        control_id = _control_id_for_guard_row(row["guard"])
        if control_id is not None:
            row_control_ids.add(control_id)

    # Body ratchet declarations: every body line carrying one of the
    # declared ratchet phrases MUST carry an inline control id, and
    # the id MUST appear as a backticked control id at the start of
    # some Guard cell. A body ratchet without a matching row is
    # RATCHET-UNCOVERED; a row's control id with no body declaration
    # is CONTROL-ORPHAN.
    declarations = _ratchet_declarations(content)
    declared_ids = {control_id for _, control_id in declarations if control_id}
    for declaration_text, control_id in declarations:
        if not control_id:
            findings.append(
                PolicyFinding(
                    requirement_id=(f"{markers.ID_INVENTORY}:{filename}:ratchet-uncovered"),
                    path=path,
                    missing_evidence=(
                        f"body ratchet declaration is not covered: {declaration_text!r}; "
                        "every body line naming a ratcheted / MUST NOT grow / "
                        "MUST NOT increase constraint MUST end with an inline "
                        "``<!-- ralph-control: <id> -->`` token whose id appears "
                        "at the start of a Guard inventory cell"
                    ),
                    required_outcome=(
                        "append ``<!-- ralph-control: <id> -->`` to the declaring "
                        "sentence and add a Guard row whose cell starts with the "
                        "matching backticked id `` `<id>` ``"
                    ),
                )
            )
            continue
        if control_id not in row_control_ids:
            findings.append(
                PolicyFinding(
                    requirement_id=(
                        f"{markers.ID_INVENTORY}:{filename}:ratchet-uncovered:{control_id}"
                    ),
                    path=path,
                    missing_evidence=(
                        f"body ratchet declaration carries control id {control_id!r} "
                        "but no Guard inventory row begins with that id; the "
                        "declaration is uncovered"
                    ),
                    required_outcome=(
                        f"add a Guard row whose cell starts with `{control_id}` "
                        "so the ratchet declaration is linked to its row"
                    ),
                )
            )
    findings.extend(
        PolicyFinding(
            requirement_id=(f"{markers.ID_INVENTORY}:{filename}:control-orphan:{orphan_id}"),
            path=path,
            missing_evidence=(
                f"Guard inventory row carries control id {orphan_id!r} but no "
                "body ratchet declaration names that id; the row is orphaned"
            ),
            required_outcome=(
                f"add a body ratchet declaration whose inline token is "
                f"``<!-- ralph-control: {orphan_id} -->``, or remove the "
                "backticked id from the Guard cell"
            ),
        )
        for orphan_id in sorted(row_control_ids - declared_ids)
    )
    return findings


__all__ = [
    "_check_guard_inventory",
    "_control_id_for_guard_row",
    "_parse_guard_inventory",
    "_ratchet_declarations",
]

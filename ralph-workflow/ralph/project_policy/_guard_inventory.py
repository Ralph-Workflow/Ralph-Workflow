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
from typing import TYPE_CHECKING, Final

if TYPE_CHECKING:
    from collections.abc import Sequence

from ralph.project_policy import markers
from ralph.project_policy._ratchet_declarations import (
    _control_id_for_guard_row,
    _ratchet_declarations,
)
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
    """Extract consecutive table lines from the guard inventory section."""
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

    table_lines: list[str] = []
    in_table = False
    for line in section_lines:
        stripped = line.strip()
        if not stripped:
            if in_table:
                break
            continue
        if stripped.startswith("|"):
            in_table = True
            table_lines.append(stripped)
        elif in_table:
            break
    if len(table_lines) < _MIN_TABLE_LINES:
        return None
    return table_lines


def _parse_guard_inventory(
    content: str,
    *,
    expected_header: Sequence[str] | None = None,
) -> list[dict[str, str]]:
    """Parse the ``## Guard inventory`` table from a starter / project policy.

    Returns one dict per data row, with keys derived from the header columns
    (e.g. ``guard``, ``owner``, ``protected_outcome``, ``detected_condition``,
    ``claim_scope``, ``retirement_trigger``) lowercased to canonical form.
    The header row is matched case-insensitively against ``expected_header``
    (defaulting to :data:`markers.GUARD_INVENTORY_HEADER`); leading and trailing
    pipes are required, and pipes must be balanced.
    The function returns an empty list when the section is missing,
    when the table has no header, when the header doesn't match,
    when separators or trailing/leading pipes are missing, or when
    any row is malformed — the caller emits a single RWP-INVENTORY:*
    finding in those cases so malformed tables fail closed.
    """
    table_lines = _extract_table_lines(content)
    if table_lines is None:
        return []

    if expected_header is None:
        m = re.search(r"<!-- ralph-policy-schema:\s*(?:freeze\s+)?(v\d+)\s*-->", content)
        if m:
            detected_h = markers.guard_inventory_header_for_version(m.group(1))
            if detected_h is not None:
                expected_header = detected_h

    if expected_header is None:
        m = re.search(r"<!-- ralph-policy-schema:\s*(?:freeze\s+)?(v\d+)\s*-->", content)
        if m:
            detected_h = markers.guard_inventory_header_for_version(m.group(1))
            if detected_h is not None:
                expected_header = detected_h
    header = (
        tuple(expected_header) if expected_header is not None else markers.GUARD_INVENTORY_HEADER
    )
    canonical = [h.strip().lower() for h in header]

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

    # 3. Data rows: each must have leading/trailing pipe and balanced columns
    out: list[dict[str, str]] = []
    keys = [h.replace(" ", "_") for h in canonical]
    for line in table_lines[2:]:
        cells = _split_table_row(line)
        if cells is None or len(cells) != len(canonical):
            return []  # malformed row; fail closed for whole table
        out.append({k: cells[i].strip() for i, k in enumerate(keys)})
    return out


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


_CLAIM_SCOPE_SENTENCE_BOUNDARY_RE: Final[re.Pattern[str]] = re.compile(r"[.!?]+\s+|\n+")


def _claim_scope_sentence_count(cell: str) -> int:
    """Return the number of sentences in a ``Claim scope`` cell.

    The v5 plan freezes the cell to a single sentence. A sentence
    boundary is sentence-ending punctuation (``.!?``) followed by
    whitespace, or a line break. Filename-like tokens such as
    ``items.py`` or ``foo.bar.txt`` do NOT count as boundaries because
    their dots are not followed by whitespace. Empty cells return zero.
    An unterminated final segment (e.g. ``Counts items. Second sentence``)
    is preserved and counted as a sentence.
    """
    stripped = cell.strip()
    if not stripped:
        return 0
    clean = re.sub(r"[.!?]+$", "", stripped).strip()
    if not clean:
        return 1
    raw_segments: list[str] = _CLAIM_SCOPE_SENTENCE_BOUNDARY_RE.split(
        clean
    )  # cast-policy: seam: typed local ensures list[str] without cast()
    segments: list[str] = [s.strip() for s in raw_segments if s.strip()]
    return max(1, len(segments))


def _check_claim_scope_cell(
    row: dict[str, str],
    index: int,
    path: str,
    canonical_keys: Sequence[str],
) -> list[PolicyFinding]:
    """Validate one row's Claim scope cell per v5/v6 contract."""
    findings: list[PolicyFinding] = []
    if "claim_scope" not in canonical_keys:
        return findings
    claim_scope = row.get("claim_scope", "")
    if not claim_scope:
        findings.append(
            PolicyFinding(
                requirement_id=markers.CLAIM_SCOPE_MISSING_FINDING_ID,
                path=path,
                missing_evidence=(
                    f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                    "has an empty 'Claim scope' cell; every row MUST "
                    "name the narrow claim its detector supports"
                ),
                required_outcome=(
                    f"fill the 'Claim scope' cell of row {index} with a "
                    "single-sentence statement of what the detector "
                    "actually observes"
                ),
            )
        )
    elif _claim_scope_sentence_count(claim_scope) > 1:
        findings.append(
            PolicyFinding(
                requirement_id=markers.CLAIM_SCOPE_MULTI_SENTENCE_FINDING_ID,
                path=path,
                missing_evidence=(
                    f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                    "has a multi-sentence 'Claim scope' cell; the v5 "
                    "plan freezes the cell to a single sentence so the "
                    "claim stays narrow and observable"
                ),
                required_outcome=(
                    f"rewrite the 'Claim scope' cell of row {index} as "
                    "exactly one sentence naming what the detector "
                    "actually observes"
                ),
            )
        )
    return findings


def _check_enforcement_cell(
    row: dict[str, str],
    index: int,
    path: str,
    known_enforcers: frozenset[str] | None,
) -> list[PolicyFinding]:
    """Validate one row's Enforcement cell per v6 contract."""
    findings: list[PolicyFinding] = []
    enforcement = row.get("enforcement", "").strip()
    if not enforcement:
        findings.append(
            PolicyFinding(
                requirement_id=markers.ENFORCEMENT_MISSING_FINDING_ID,
                path=path,
                missing_evidence=(
                    f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                    "has an empty 'Enforcement' cell; every row MUST "
                    "declare its enforcement mode"
                ),
                required_outcome=(
                    f"fill the 'Enforcement' cell of row {index} with "
                    "'deterministic: <id>' or 'advisory: <reason>; review: <trigger>'"
                ),
            )
        )
    elif enforcement.startswith("deterministic:"):
        enforcer_id = enforcement[len("deterministic:") :].strip()
        if not enforcer_id:
            findings.append(
                PolicyFinding(
                    requirement_id=markers.ENFORCEMENT_MISSING_FINDING_ID,
                    path=path,
                    missing_evidence=(
                        f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                        "has 'deterministic:' with no enforcer id"
                    ),
                    required_outcome=(f"provide a non-empty enforcer id for row {index}"),
                )
            )
        elif known_enforcers is not None and enforcer_id not in known_enforcers:
            findings.append(
                PolicyFinding(
                    requirement_id=markers.ENFORCEMENT_UNKNOWN_ID_FINDING_ID,
                    path=path,
                    missing_evidence=(
                        f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                        f"references unknown enforcer id {enforcer_id!r}"
                    ),
                    required_outcome=(
                        f"ensure {enforcer_id!r} matches a declared "
                        "RALPH-COMMAND value or verify.py step label"
                    ),
                )
            )
    elif enforcement.startswith("advisory:"):
        if "; review:" not in enforcement:
            findings.append(
                PolicyFinding(
                    requirement_id=markers.ENFORCEMENT_ADVISORY_NO_TRIGGER_FINDING_ID,
                    path=path,
                    missing_evidence=(
                        f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                        "advisory enforcement lacks '; review: <trigger>'"
                    ),
                    required_outcome=(
                        f"add '; review: <trigger>' to row {index} advisory enforcement"
                    ),
                )
            )
        else:
            _, review_trigger = enforcement.split("; review:", 1)
            if not review_trigger.strip():
                findings.append(
                    PolicyFinding(
                        requirement_id=markers.ENFORCEMENT_ADVISORY_NO_TRIGGER_FINDING_ID,
                        path=path,
                        missing_evidence=(
                            f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                            "advisory enforcement has an empty review trigger"
                        ),
                        required_outcome=(
                            f"provide a non-empty review trigger after '; review:' for row {index}"
                        ),
                    )
                )
    else:
        findings.append(
            PolicyFinding(
                requirement_id=markers.ENFORCEMENT_MISSING_FINDING_ID,
                path=path,
                missing_evidence=(
                    f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                    f"has invalid enforcement {enforcement!r}; must be "
                    "'deterministic: <id>' or 'advisory: <reason>; review: <trigger>'"
                ),
                required_outcome=(
                    f"change enforcement for row {index} to "
                    "'deterministic: <id>' or 'advisory: <reason>; review: <trigger>'"
                ),
            )
        )
    return findings


def _check_guard_inventory(
    content: str,
    path: str,
    filename: str,
    *,
    expected_header: Sequence[str] | None = None,
    known_enforcers: frozenset[str] | None = None,
) -> list[PolicyFinding]:
    """Validate the ``## Guard inventory`` table per v5 contract (B9).

    Findings emitted (stable v5 contract):

    * ``RWP-INVENTORY:<file>:missing`` when the section is missing
      entirely.
    * ``RWP-INVENTORY:<file>:malformed`` when the header row does not
      match :data:`markers.GUARD_INVENTORY_HEADER`.
    * ``RWP-INVENTORY:<file>:row-<n>:empty-<column>`` per row with an
      empty cell in any of the five base columns (the ``Claim scope``
      column uses the stable :data:`CLAIM_SCOPE_MISSING_FINDING_ID`
      instead, see below).
    * ``RWP-INVENTORY:CLAIM-SCOPE-MISSING`` per row with an empty
      ``Claim scope`` cell. The plan freezes the v5 contract id;
      filename/row suffixes would break black-box tests that pin
      the id.
    * ``RWP-INVENTORY:CLAIM-SCOPE-MULTI-SENTENCE`` per row whose
      ``Claim scope`` cell is more than one sentence. The plan
      freezes the cell to a single sentence; multi-sentence prose
      is rejected with a stable id.
    * ``RWP-INVENTORY:<file>:row-<n>:claim-scope`` per row whose
      ``Protected outcome`` or ``Claim scope`` cell carries a
      broad-guarantee phrase from
      :data:`markers.CLAIM_SCOPE_PHRASES` that does not also appear
      in the row's ``Detected condition`` (the deterministic
      claim-scope rule, B7).
    * ``RWP-INVENTORY:RATCHET-UNCOVERED`` per body ratchet sentence
      that has no inline ``<!-- ralph-control: <id> -->`` token
      (filename suffix is intentionally absent so the id is stable
      across files).
    * ``RWP-INVENTORY:RATCHET-UNCOVERED:<id>`` per body ratchet
      sentence whose control id does not appear at the start of any
      Guard cell.
    * ``RWP-INVENTORY:CONTROL-ORPHAN:<id>`` per Guard cell control
      id that no body declaration names.

    The check returns an empty list when the table is well-formed:
    no findings means the starter is B9-compliant.
    """
    if expected_header is None:
        m = re.search(r"<!-- ralph-policy-schema:\s*(?:freeze\s+)?(v\d+)\s*-->", content)
        if m:
            detected_h = markers.guard_inventory_header_for_version(m.group(1))
            if detected_h is not None:
                expected_header = detected_h
    header = (
        tuple(expected_header) if expected_header is not None else markers.GUARD_INVENTORY_HEADER
    )
    if not _starts_with_heading(content, markers.GUARD_INVENTORY_HEADING):
        return [
            PolicyFinding(
                requirement_id=f"{markers.ID_INVENTORY}:{filename}:missing",
                path=path,
                missing_evidence=(
                    f"file lacks a {markers.GUARD_INVENTORY_HEADING} section; "
                    "every applicable starter must enumerate its guards with "
                    f"the pinned {len(header)}-cell row"
                ),
                required_outcome=(
                    f"add a `{markers.GUARD_INVENTORY_HEADING}` section "
                    "with the pinned header row and one data row per guard "
                    f"({' | '.join(header)})"
                ),
            )
        ]
    rows = _parse_guard_inventory(content, expected_header=header)
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
                    f"({' | '.join(header)}) and "
                    "include at least one data row"
                ),
            )
        ]
    findings: list[PolicyFinding] = []
    row_control_ids: set[str] = set()
    canonical_keys = [h.strip().lower().replace(" ", "_") for h in header]
    base_columns = [col for col in canonical_keys if col not in ("claim_scope", "enforcement")]
    for index, row in enumerate(rows, start=1):
        for column in base_columns:
            if row.get(column):
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
        # Stable v5 contract: an empty ``Claim scope`` cell emits the
        # plan-frozen finding id ``RWP-INVENTORY:CLAIM-SCOPE-MISSING``
        # (no filename/row suffix). A multi-sentence ``Claim scope``
        # cell emits ``RWP-INVENTORY:CLAIM-SCOPE-MULTI-SENTENCE`` for
        # the same reason.
        findings.extend(_check_claim_scope_cell(row, index, path, canonical_keys))
        if "enforcement" in canonical_keys:
            findings.extend(_check_enforcement_cell(row, index, path, known_enforcers))
        # Apply the deterministic claim-scope rule to each row's
        # ``Protected outcome`` AND ``Claim scope`` cells. The same
        # predicate fires on a broad phrase absent from the row's
        # ``Detected condition`` for both cells.
        outcome = row.get("protected_outcome", "").lower()
        scope = row.get("claim_scope", "").lower()
        detected = row.get("detected_condition", "").lower()
        findings.extend(_claim_scope_findings(filename, path, index, outcome, detected))
        if scope:
            findings.extend(_claim_scope_findings(filename, path, index, scope, detected))
        # Track the row's leading control id, if any, for the
        # orphan / uncovered checks below. A row without a leading
        # backticked id does NOT participate in the linkage scan.
        control_id = _control_id_for_guard_row(row.get("guard", ""))
        if control_id is not None:
            row_control_ids.add(control_id)

    # Body ratchet declarations: every body sentence carrying one of
    # the declared ratchet phrases MUST carry an inline control id,
    # and the id MUST appear as a backticked control id at the start
    # of some Guard cell. A body ratchet without a matching row is
    # RATCHET-UNCOVERED (stable v5 contract id); a row's control id
    # with no body declaration is CONTROL-ORPHAN.
    declarations = _ratchet_declarations(content)
    declared_ids = {control_id for _, control_id in declarations if control_id}
    for declaration_text, control_id in declarations:
        if not control_id:
            findings.append(
                PolicyFinding(
                    requirement_id=markers.RATCHET_UNCOVERED_FINDING_ID,
                    path=path,
                    missing_evidence=(
                        f"body ratchet sentence is not covered: {declaration_text!r}; "
                        "every body sentence naming a ratcheted / ratchet / "
                        "MUST NOT grow / MUST NOT increase constraint MUST end "
                        "with an inline ``<!-- ralph-control: <id> -->`` token "
                        "whose id appears at the start of a Guard inventory cell"
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
                    requirement_id=(f"{markers.RATCHET_UNCOVERED_FINDING_ID}:{control_id}"),
                    path=path,
                    missing_evidence=(
                        f"body ratchet sentence carries control id {control_id!r} "
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
            requirement_id=(f"{markers.CONTROL_ORPHAN_FINDING_ID}:{orphan_id}"),
            path=path,
            missing_evidence=(
                f"Guard inventory row carries control id {orphan_id!r} but no "
                "body ratchet sentence names that id; the row is orphaned"
            ),
            required_outcome=(
                f"add a body ratchet sentence whose inline token is "
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

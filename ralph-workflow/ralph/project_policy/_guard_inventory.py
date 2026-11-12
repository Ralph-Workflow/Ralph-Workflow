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
    stripped = line.strip()
    if (
        len(stripped) < _MIN_TABLE_ROW_LENGTH
        or not stripped.startswith("|")
        or not stripped.endswith("|")
    ):
        return None
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


def _is_table_separator(line: str, *, expected_columns: int) -> bool:
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
    table_lines = _extract_table_lines(content)
    if table_lines is None:
        return []

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

    header_cells = _split_table_row(table_lines[0])
    valid_header = (
        header_cells is not None
        and len(header_cells) == len(canonical)
        and [c.strip().lower() for c in header_cells] == canonical
    )
    valid_sep = _is_table_separator(table_lines[1], expected_columns=len(canonical))
    if not (valid_header and valid_sep):
        return []

    out: list[dict[str, str]] = []
    keys = [h.replace(" ", "_") for h in canonical]
    for line in table_lines[2:]:
        cells = _split_table_row(line)
        if cells is None or len(cells) != len(canonical):
            return []
        out.append({k: cells[i].strip() for i, k in enumerate(keys)})
    return out


def _starts_with_heading(content: str, heading: str) -> bool:
    return any(line.strip() == heading for line in content.splitlines())


def _claim_scope_findings(
    filename: str,
    path: str,
    index: int,
    outcome: str,
    detected: str,
) -> list[PolicyFinding]:
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
    stripped = cell.strip()
    if not stripped:
        return 0
    clean = re.sub(r"[.!?]+$", "", stripped).strip()
    if not clean:
        return 1
    raw_segments: list[str] = _CLAIM_SCOPE_SENTENCE_BOUNDARY_RE.split(clean)
    segments: list[str] = [s.strip() for s in raw_segments if s.strip()]
    return max(1, len(segments))


def _check_claim_scope_cell(
    row: dict[str, str],
    index: int,
    path: str,
    canonical_keys: Sequence[str],
) -> list[PolicyFinding]:
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
            reason_part, review_trigger = enforcement.split("; review:", 1)
            reason = reason_part[len("advisory:") :].strip()
            if not reason:
                findings.append(
                    PolicyFinding(
                        requirement_id=markers.ENFORCEMENT_MISSING_FINDING_ID,
                        path=path,
                        missing_evidence=(
                            f"{markers.GUARD_INVENTORY_HEADING} row {index} "
                            "advisory enforcement has an empty reason"
                        ),
                        required_outcome=(
                            f"provide a non-empty reason after 'advisory:' for row {index}"
                        ),
                    )
                )
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
        findings.extend(_check_claim_scope_cell(row, index, path, canonical_keys))
        if "enforcement" in canonical_keys:
            findings.extend(_check_enforcement_cell(row, index, path, known_enforcers))
        outcome = row.get("protected_outcome", "").lower()
        scope = row.get("claim_scope", "").lower()
        detected = row.get("detected_condition", "").lower()
        findings.extend(_claim_scope_findings(filename, path, index, outcome, detected))
        if scope:
            findings.extend(_claim_scope_findings(filename, path, index, scope, detected))
        control_id = _control_id_for_guard_row(row.get("guard", ""))
        if control_id is not None:
            row_control_ids.add(control_id)

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

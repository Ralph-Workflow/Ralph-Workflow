from __future__ import annotations

import re
from pathlib import Path

from ralph.project_policy import markers, starters

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "bounded_deterministic_evidence_rule": (
        "PRODUCT_CRITERIA ground rule 3 allows `deterministic` only for `strong` or "
        "`moderate` rules; the comment and docstring ban is the single bounded "
        "exception, so the broad any-tag wording and its contract statements are "
        "replaced"
    ),
}

AGENT_RULES: tuple[tuple[int, str], ...] = (
    (1, "Check first"),
    (2, "Real-artifact evidence"),
    (3, "Test integrity"),
    (4, "Regression tests from the spec"),
    (5, "Independent review"),
    (6, "Handoff by artifact"),
    (7, "Smallest change"),
    (8, "Context hygiene"),
    (9, "Plan scaling"),
    (10, "Commit-level hygiene"),
    (11, "Human checkpoints"),
    (12, "Rule-to-enforcer ladder"),
    (13, "Non-inferable instruction surface"),
)

EVIDENCE_TAG_PATTERN = re.compile(r"\(evidence:\s*(strong|moderate|anecdotal|none)\)")

DETERMINISTIC_TAG_ANCHOR: str = (
    "Only a rule tagged `strong` or `moderate` may carry a `deterministic` enforcement status"
)

RETIRED_BROAD_DETERMINISTIC_ANCHORS: tuple[str, ...] = (
    "`deterministic` whatever its evidence tag",
    "does not make a rule advisory when a deterministic gate decides it",
)

COMMENT_BAN_ANCHOR: str = (
    "two exceptions are the absolute comment and docstring ban and the "
    "banned-construct ban in `clean-code-policy.md`, project rules tagged `none`"
)

GUARD_INVENTORY_COLUMNS: tuple[str, ...] = (
    "Guard",
    "Owner",
    "Protected outcome",
    "Detected condition",
    "Claim scope",
    "Enforcement",
    "Retirement trigger",
)

SPEC_VALUE_GUARD_NAME: str = "Regression tests from the spec"

SPEC_VALUE_ADVISORY_REASON_SUBSTRINGS: tuple[str, ...] = ("expected value came from the spec",)

SPEC_VALUE_REVIEW_TRIGGER_SUBSTRINGS: tuple[str, ...] = (
    "expected value copied from the implementation",
)

RETIRED_PRE_FIX_REASON_SUBSTRINGS: tuple[str, ...] = (
    "pre-fix failure",
    "failed before the fix",
)

IDENTICAL_AGENT_RULE_TITLES: tuple[str, ...] = (
    "Check first",
    "Real-artifact evidence",
    "Regression tests from the spec",
    "Handoff by artifact",
    "Smallest change",
    "Context hygiene",
    "Plan scaling",
    "Commit-level hygiene",
    "Human checkpoints",
    "Rule-to-enforcer ladder",
    "Non-inferable instruction surface",
)

PROJECT_SPECIFIC_AGENT_RULE_TITLES: tuple[str, ...] = (
    "Test integrity",
    "Independent review",
)

SMALLEST_CHANGE_RULE_TITLE: str = "Smallest change"

SMALLEST_CHANGE_OBLIGATION: str = "deletes the replaced API in the same change"

INVERTED_SMALLEST_CHANGE_OBLIGATION: str = "deletes its replacement in the same change"

COMMENT_BAN_EVIDENCE_PARAGRAPH_START: str = "Evidence tags differ by claim."

COMMENT_BAN_EVIDENCE_PARAGRAPH_END: str = "There is no inline bypass marker"

COMMENT_BAN_CURRENT_FACT_STATEMENTS: tuple[str, ...] = (
    "The ban itself is a project judgment tagged `none`",
    "no recorded source establishes that the ban lowers the defect rate",
    "the ban does not rest on one",
    "The evidence tag and the enforcement status are separate properties",
    "the tag names the kind of source behind the rule, the enforcement status names who decides it",
    "The ban ships as `deterministic` because the comment audit enforces it",
    "it is one of two exceptions (along with the banned-construct ban) to the "
    "execution-kernel rule that a rule tagged `anecdotal` or `none` ships advisory",
)

STARTER_COMMENT_BAN_ENFORCEMENT: str = "deterministic: PROJECT-FACT-UNRESOLVED"


def starter(name: str) -> str:
    return starters.read_starter(name)


def kernel() -> str:
    return starters.read_starter("execution-kernel.md")


def root_kernel() -> str:
    path = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "ralph-workflow-policy"
        / "execution-kernel.md"
    )
    assert path.is_file(), f"root kernel file not found at {path}"
    return path.read_text(encoding="utf-8")


def root_policy(name: str) -> str:
    path = Path(__file__).resolve().parents[3] / "docs" / "ralph-workflow-policy" / name
    assert path.is_file(), f"root policy file not found at {path}"
    return path.read_text(encoding="utf-8")


def section_of(content: str, heading: str) -> str:
    marker = f"\n{heading}\n"
    assert marker in content, f"missing {heading!r} section"
    start = content.index(marker) + len(marker)
    rest = content[start:]
    next_heading = rest.find("\n## ")
    return rest[:next_heading] if next_heading != -1 else rest


def kernel_rules_section(content: str) -> str:
    heading = "## Kernel rules"
    assert heading in content, f"kernel starter missing {heading!r} section"
    start = content.index(heading) + len(heading)
    rest = content[start:]
    next_heading = rest.find("\n## ")
    return rest[:next_heading] if next_heading != -1 else rest


def rule_statements(section: str) -> dict[str, str]:
    pattern = re.compile(r"((?:\d+\.|\*)\s+\*\*(.*?)\*\*[\s\S]*?)(?=\n(?:\d+\.|\*)\s+\*\*|\Z)")
    rules: dict[str, str] = {}
    for match in pattern.finditer(section):
        block: str = match.group(1)
        raw_title: str = match.group(2)
        title: str = raw_title.rstrip(".:").strip().lower()
        rules[title] = " ".join(block.split())
    return rules


def agent_rule_statements(content: str) -> dict[str, str]:
    return rule_statements(section_of(content, "## Agent rules"))


def agent_rule_body(content: str, title: str) -> str:
    statement = agent_rule_statements(content).get(title.lower())
    assert statement is not None, f"no {title!r} agent rule in the policy file"
    marker = f"**{title.lower()}"
    index = statement.lower().find(marker)
    assert index != -1, f"the {title!r} agent rule carries no bold title"
    body = statement[index + len(marker) :]
    return " ".join(body.lstrip(".:").lstrip("*").split())


def commenting_evidence_paragraph(content: str) -> str:
    normalized = " ".join(content.split())
    start = normalized.find(COMMENT_BAN_EVIDENCE_PARAGRAPH_START)
    assert start != -1, f"the policy file has no {COMMENT_BAN_EVIDENCE_PARAGRAPH_START!r} paragraph"
    rest = normalized[start:]
    end = rest.find(COMMENT_BAN_EVIDENCE_PARAGRAPH_END)
    assert end != -1, (
        f"the comment-ban evidence paragraph has no {COMMENT_BAN_EVIDENCE_PARAGRAPH_END!r} boundary"
    )
    return rest[:end]


def evidence_tag(statement: str) -> str:
    match = EVIDENCE_TAG_PATTERN.search(statement)
    assert match is not None, "rule statement carries no evidence tag"
    return match.group(1)


def enforcer_ladder_rows(content: str) -> list[tuple[int, str, str]]:
    section = section_of(content, "## Rule-to-enforcer ladder")
    rows: list[tuple[int, str, str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
        if len(cells) != 4:
            continue
        match = re.match(r"^(\d+)\s+(.*)$", cells[0])
        if match is None:
            continue
        rows.append((int(match.group(1)), match.group(2), cells[3]))
    return rows


def enforcer_status_rows(content: str) -> dict[str, str]:
    return {rule: status for _number, rule, status in enforcer_ladder_rows(content)}


def numbered_enforcer_rows(content: str) -> list[tuple[int, str]]:
    return [(number, rule) for number, rule, _status in enforcer_ladder_rows(content)]


def guard_inventory_table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.split("|")[1:-1]]


def guard_inventory_rows(content: str) -> list[list[str]]:
    section = section_of(content, markers.GUARD_INVENTORY_HEADING)
    table_rows: list[list[str]] = []
    for line in section.splitlines():
        cells = guard_inventory_table_cells(line)
        if not cells:
            continue
        if all(cell and set(cell) <= set("-:") for cell in cells):
            continue
        table_rows.append(cells)
    assert table_rows, "guard inventory carries no table rows"
    assert [cell.lower() for cell in table_rows[0]] == [
        column.lower() for column in GUARD_INVENTORY_COLUMNS
    ], (
        f"guard inventory header {table_rows[0]} is not the seven canonical columns "
        f"{list(GUARD_INVENTORY_COLUMNS)}"
    )
    return table_rows[1:]


def guard_row(content: str, guard_name: str, source_name: str) -> list[str]:
    rows = guard_inventory_rows(content)
    matching = [row for row in rows if row[0] == guard_name]
    assert matching, (
        f"{source_name} guard inventory has no row whose Guard cell is exactly {guard_name!r}"
    )
    row = matching[0]
    assert len(row) == len(GUARD_INVENTORY_COLUMNS), (
        f"{source_name} guard-inventory row for {guard_name!r} carries {len(row)} cells; "
        f"the contract is {len(GUARD_INVENTORY_COLUMNS)} cells: {list(GUARD_INVENTORY_COLUMNS)}"
    )
    empty_columns = [
        column for column, cell in zip(GUARD_INVENTORY_COLUMNS, row, strict=True) if not cell
    ]
    assert not empty_columns, (
        f"{source_name} guard-inventory row for {guard_name!r} has empty cells: {empty_columns}"
    )
    return row


def enforcement_cell(row: list[str]) -> str:
    return row[GUARD_INVENTORY_COLUMNS.index("Enforcement")]


def spec_value_guard_row(content: str, source_name: str) -> list[str]:
    rows = guard_inventory_rows(content)
    matching = [row for row in rows if row[0] == SPEC_VALUE_GUARD_NAME]
    assert matching, (
        f"{source_name} guard inventory has no row whose Guard cell is exactly "
        f"{SPEC_VALUE_GUARD_NAME!r}; a fresh ralph --init project would ship without "
        f"the regression-test guard"
    )
    row = matching[0]
    assert len(row) == len(GUARD_INVENTORY_COLUMNS), (
        f"{source_name} guard-inventory row for {SPEC_VALUE_GUARD_NAME!r} carries "
        f"{len(row)} cells; the contract is {len(GUARD_INVENTORY_COLUMNS)} cells: "
        f"{list(GUARD_INVENTORY_COLUMNS)}"
    )
    empty_columns = [
        column for column, cell in zip(GUARD_INVENTORY_COLUMNS, row, strict=True) if not cell
    ]
    assert not empty_columns, (
        f"{source_name} guard-inventory row for {SPEC_VALUE_GUARD_NAME!r} has empty "
        f"cells: {empty_columns}"
    )
    return row


def advisory_reason_and_trigger(
    enforcement: str,
    guard_name: str,
    source_name: str,
) -> tuple[str, str]:
    lowered = enforcement.lower()
    assert lowered.startswith("advisory:"), (
        f"{source_name} guard-inventory row for {guard_name!r} must keep an "
        f"advisory enforcement; observed: {enforcement!r}"
    )
    marker = "; review:"
    index = lowered.find(marker)
    assert index != -1, (
        f"{source_name} guard-inventory row for {guard_name!r} must name its "
        f"review trigger; observed: {enforcement!r}"
    )
    reason = enforcement[len("advisory:") : index].strip()
    trigger = enforcement[index + len(marker) :].strip()
    return reason, trigger


def assert_spec_value_guard_contract(content: str, source_name: str) -> str:
    row = spec_value_guard_row(content, source_name)
    enforcement = row[GUARD_INVENTORY_COLUMNS.index("Enforcement")]
    protected_outcome = row[GUARD_INVENTORY_COLUMNS.index("Protected outcome")]
    claim_scope = row[GUARD_INVENTORY_COLUMNS.index("Claim scope")]
    reason, trigger = advisory_reason_and_trigger(enforcement, SPEC_VALUE_GUARD_NAME, source_name)
    reason_lower = reason.lower()
    for substring in SPEC_VALUE_ADVISORY_REASON_SUBSTRINGS:
        assert substring in reason_lower, (
            f"{source_name} guard-inventory row for {SPEC_VALUE_GUARD_NAME!r} does not "
            f"state the advisory reason {substring!r}; observed reason: {reason!r}"
        )
    trigger_lower = trigger.lower()
    assert any(substring in trigger_lower for substring in SPEC_VALUE_REVIEW_TRIGGER_SUBSTRINGS), (
        f"{source_name} guard-inventory row for {SPEC_VALUE_GUARD_NAME!r} names no review "
        f"trigger about an expected value copied from the implementation; observed "
        f"trigger: {trigger!r}"
    )
    assert "expected value" in protected_outcome.lower(), (
        f"{source_name} guard-inventory Protected outcome for {SPEC_VALUE_GUARD_NAME!r} "
        f"does not name the expected value; observed: {protected_outcome!r}"
    )
    assert "spec" in protected_outcome.lower(), (
        f"{source_name} guard-inventory Protected outcome for {SPEC_VALUE_GUARD_NAME!r} "
        f"does not name the spec; observed: {protected_outcome!r}"
    )
    assert "expected value" in claim_scope.lower(), (
        f"{source_name} guard-inventory Claim scope for {SPEC_VALUE_GUARD_NAME!r} does not "
        f"name the expected value; observed: {claim_scope!r}"
    )
    return enforcement


def replace_guard_inventory_section(content: str, replacement: str) -> str:
    marker = f"\n{markers.GUARD_INVENTORY_HEADING}\n"
    start = content.index(marker) + len(marker)
    rest = content[start:]
    next_heading = rest.find("\n## ")
    end = start + (next_heading if next_heading != -1 else len(rest))
    return content[:start] + replacement + content[end:]


def mutated_guard_inventory_texts(content: str) -> tuple[str, str]:
    section = section_of(content, markers.GUARD_INVENTORY_HEADING)
    enforcement_index = GUARD_INVENTORY_COLUMNS.index("Enforcement")
    without_row: list[str] = []
    without_reason: list[str] = []
    guard_rows = 0
    for line in section.splitlines(keepends=True):
        cells = guard_inventory_table_cells(line)
        is_guard_row = bool(cells) and cells[0] == SPEC_VALUE_GUARD_NAME
        if not is_guard_row:
            without_row.append(line)
            without_reason.append(line)
            continue
        guard_rows += 1
        without_row.append("")
        _reason, trigger = advisory_reason_and_trigger(
            cells[enforcement_index], SPEC_VALUE_GUARD_NAME, "root kernel"
        )
        mutated_cells = list(cells)
        mutated_cells[enforcement_index] = f"advisory: ; review: {trigger}"
        ending = "\n" if line.endswith("\n") else ""
        without_reason.append("| " + " | ".join(mutated_cells) + " |" + ending)
    assert guard_rows == 1, (
        f"expected exactly one {SPEC_VALUE_GUARD_NAME!r} row in the guard inventory; "
        f"found {guard_rows}"
    )
    return (
        replace_guard_inventory_section(content, "".join(without_row)),
        replace_guard_inventory_section(content, "".join(without_reason)),
    )

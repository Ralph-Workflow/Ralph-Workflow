from __future__ import annotations

import re
from pathlib import Path

from ralph.project_policy import starters

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_kernels_state_the_non_inferable_instruction_surface_rule": (
        "the kernel now states the non-inferable instruction-surface rule, so both "
        "kernels must carry it with its strong evidence tag, its non-inferable "
        "content obligations, and its advisory enforcement with a review trigger"
    ),
    "test_kernel_root_and_starter_carry_the_same_instruction_surface_row": (
        "the instruction-surface guard row is a contract the root pack and the "
        "starter share, so both must carry the same advisory enforcement cell and "
        "the ladder cell that repeats it"
    ),
    "test_kernels_state_the_pre_fix_failure_obligation": (
        "the v6 brief requires a bug-fix regression test to fail on the pre-fix "
        "revision; the retirement contradicted the brief, so the check that "
        "required the pre-fix failure is restored as its own contract test"
    ),
    "test_kernels_state_the_spec_derived_expected_value_obligation": (
        "the v6 brief requires a bug-fix regression test to fail on the pre-fix "
        "revision, so the pre-fix-failure clause is an obligation of the "
        "regression-tests rule while the failing-run-record clause stays retired; "
        "the kernel states the spec-derived expected value and the "
        "library-documentation clause"
    ),
    "test_kernels_drop_the_rule_history_sentence": (
        "the kernel carried a sentence narrating where the rules used to live, "
        "which the shipped documentation restriction bans, so its absence is now "
        "pinned in both kernels"
    ),
}

INSTRUCTION_SURFACE_RULE_NAME = "Non-inferable instruction surface"

INSTRUCTION_SURFACE_OBLIGATIONS: tuple[str, ...] = (
    "verify commands",
    "run-one-test command",
    "misleading code",
    "beyond the repository",
    "corrections the agent must repeat",
    "llm-generated bulk summary of the codebase is excluded",
)

SPEC_VALUE_OBLIGATIONS: tuple[str, ...] = (
    "expected value is written down from the spec",
    "that library's own documentation is the spec",
    "fails on the pre-fix revision",
)

RETIRED_RED_GREEN_CLAUSES: tuple[str, ...] = ("the failing run is recorded with the change",)

RULE_HISTORY_SENTENCE_FRAGMENTS: tuple[str, ...] = (
    "previously appeared verbatim",
    "they live here exactly once",
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

EVIDENCE_TAG_PATTERN = re.compile(r"\(evidence:\s*(strong|moderate|anecdotal|none)\)")


def _root_kernel() -> str:
    path = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "ralph-workflow-policy"
        / "execution-kernel.md"
    )
    assert path.is_file(), f"root kernel file not found at {path}"
    return path.read_text(encoding="utf-8")


def _starter_kernel() -> str:
    return starters.read_starter("execution-kernel.md")


def _section(content: str, heading: str) -> str:
    marker = f"\n{heading}\n"
    assert marker in content, f"missing {heading!r} section"
    start = content.index(marker) + len(marker)
    rest = content[start:]
    next_heading = rest.find("\n## ")
    return rest[:next_heading] if next_heading != -1 else rest


def _agent_rule_statements(content: str) -> dict[str, str]:
    section = _section(content, "## Agent rules")
    pattern = re.compile(r"((?:\d+\.|\*)\s+\*\*(.*?)\*\*[\s\S]*?)(?=\n(?:\d+\.|\*)\s+\*\*|\Z)")
    rules: dict[str, str] = {}
    for match in pattern.finditer(section):
        title = match.group(2).rstrip(".:").strip().lower()
        rules[title] = " ".join(match.group(1).split())
    return rules


def _evidence_tag(statement: str) -> str:
    match = EVIDENCE_TAG_PATTERN.search(statement)
    assert match is not None, "rule statement carries no evidence tag"
    return match.group(1)


def _enforcer_status_rows(content: str) -> dict[str, str]:
    section = _section(content, "## Rule-to-enforcer ladder")
    rows: dict[str, str] = {}
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
        rows[match.group(2)] = cells[3]
    return rows


def _guard_inventory_rows(content: str) -> list[list[str]]:
    section = _section(content, "## Guard inventory")
    table_rows: list[list[str]] = []
    for line in section.splitlines():
        stripped = line.strip()
        if not stripped.startswith("|"):
            continue
        cells = [cell.strip() for cell in stripped.split("|")[1:-1]]
        if cells and all(cell and set(cell) <= set("-:") for cell in cells):
            continue
        table_rows.append(cells)
    assert table_rows, "guard inventory carries no table rows"
    return table_rows[1:]


def _guard_row(content: str, guard_name: str, source_name: str) -> list[str]:
    matching = [row for row in _guard_inventory_rows(content) if row and row[0] == guard_name]
    assert matching, (
        f"{source_name} guard inventory has no row whose Guard cell is exactly {guard_name!r}"
    )
    row = matching[0]
    assert len(row) == len(GUARD_INVENTORY_COLUMNS), (
        f"{source_name} guard-inventory row for {guard_name!r} carries {len(row)} "
        f"cells; the contract is {len(GUARD_INVENTORY_COLUMNS)} cells: "
        f"{list(GUARD_INVENTORY_COLUMNS)}"
    )
    return row


def test_kernels_state_the_non_inferable_instruction_surface_rule() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _starter_kernel())):
        statements = _agent_rule_statements(content)
        statement = statements.get(INSTRUCTION_SURFACE_RULE_NAME.lower())
        assert statement is not None, (
            f"{source_name} kernel does not state the {INSTRUCTION_SURFACE_RULE_NAME!r} agent rule"
        )
        assert _evidence_tag(statement) == "strong", (
            f"{source_name} kernel rule {INSTRUCTION_SURFACE_RULE_NAME!r} carries "
            f"evidence tag {_evidence_tag(statement)!r}; the research basis names a "
            "controlled study, so the tag is 'strong'"
        )
        normalized = " ".join(statement.lower().split())
        for obligation in INSTRUCTION_SURFACE_OBLIGATIONS:
            assert obligation in normalized, (
                f"{source_name} kernel rule {INSTRUCTION_SURFACE_RULE_NAME!r} does not "
                f"state the non-inferable-content obligation {obligation!r}"
            )
        status = _enforcer_status_rows(content).get(INSTRUCTION_SURFACE_RULE_NAME)
        assert status is not None, (
            f"{source_name} kernel rule-to-enforcer ladder has no row for the "
            f"{INSTRUCTION_SURFACE_RULE_NAME!r} rule"
        )
        assert status.startswith("advisory:"), (
            f"{source_name} kernel rule {INSTRUCTION_SURFACE_RULE_NAME!r} must keep its "
            f"advisory enforcement; observed: {status!r}"
        )
        assert "; review:" in status, (
            f"{source_name} kernel rule {INSTRUCTION_SURFACE_RULE_NAME!r} must name its "
            f"review trigger; observed: {status!r}"
        )


def test_kernel_root_and_starter_carry_the_same_instruction_surface_row() -> None:
    root_row = _guard_row(_root_kernel(), INSTRUCTION_SURFACE_RULE_NAME, "root kernel")
    starter_row = _guard_row(_starter_kernel(), INSTRUCTION_SURFACE_RULE_NAME, "starter kernel")
    enforcement_index = GUARD_INVENTORY_COLUMNS.index("Enforcement")
    root_enforcement = root_row[enforcement_index]
    starter_enforcement = starter_row[enforcement_index]
    for source_name, enforcement in (
        ("root kernel", root_enforcement),
        ("starter kernel", starter_enforcement),
    ):
        assert enforcement.startswith("advisory:"), (
            f"{source_name} guard-inventory row for {INSTRUCTION_SURFACE_RULE_NAME!r} must "
            f"carry an advisory enforcement; observed: {enforcement!r}"
        )
        assert "; review:" in enforcement, (
            f"{source_name} guard-inventory row for {INSTRUCTION_SURFACE_RULE_NAME!r} must "
            f"name its review trigger; observed: {enforcement!r}"
        )
    assert " ".join(root_enforcement.lower().split()) == " ".join(
        starter_enforcement.lower().split()
    ), (
        f"root kernel instruction-surface enforcement {root_enforcement!r} differs from "
        f"the starter kernel enforcement {starter_enforcement!r}"
    )
    root_status = _enforcer_status_rows(_root_kernel()).get(INSTRUCTION_SURFACE_RULE_NAME)
    starter_status = _enforcer_status_rows(_starter_kernel()).get(INSTRUCTION_SURFACE_RULE_NAME)
    assert root_status == root_enforcement, (
        "root kernel rule-to-enforcer ladder must repeat the guard-inventory "
        f"Enforcement cell verbatim; ladder: {root_status!r}; inventory: "
        f"{root_enforcement!r}"
    )
    assert starter_status == starter_enforcement, (
        "starter kernel rule-to-enforcer ladder must repeat the guard-inventory "
        f"Enforcement cell verbatim; ladder: {starter_status!r}; inventory: "
        f"{starter_enforcement!r}"
    )


def test_kernels_state_the_spec_derived_expected_value_obligation() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _starter_kernel())):
        statements = _agent_rule_statements(content)
        statement = statements.get("regression tests from the spec")
        assert statement is not None, (
            f"{source_name} kernel does not state the regression-tests-from-the-spec rule"
        )
        normalized = " ".join(statement.lower().split())
        for clause in SPEC_VALUE_OBLIGATIONS:
            assert clause in normalized, (
                f"{source_name} kernel rule 'Regression tests from the spec' does not "
                f"state the spec-derived expected value obligation {clause!r}"
            )
        for clause in RETIRED_RED_GREEN_CLAUSES:
            assert clause not in normalized, (
                f"{source_name} kernel rule 'Regression tests from the spec' still states "
                f"the retired generic red-green obligation {clause!r}; the technique is "
                f"taught by the test-driven-development skill"
            )


def test_kernels_state_the_pre_fix_failure_obligation() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _starter_kernel())):
        statements = _agent_rule_statements(content)
        statement = statements.get("regression tests from the spec")
        assert statement is not None, (
            f"{source_name} kernel does not state the regression-tests-from-the-spec rule"
        )
        normalized = " ".join(statement.lower().split())
        assert "fails on the pre-fix revision" in normalized, (
            f"{source_name} kernel rule 'Regression tests from the spec' does not "
            "state that the bug-fix test fails on the pre-fix revision"
        )


def test_kernels_drop_the_rule_history_sentence() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _starter_kernel())):
        normalized = " ".join(content.lower().split())
        for fragment in RULE_HISTORY_SENTENCE_FRAGMENTS:
            assert fragment not in normalized, (
                f"{source_name} kernel still narrates where the rules used to live: {fragment!r}"
            )

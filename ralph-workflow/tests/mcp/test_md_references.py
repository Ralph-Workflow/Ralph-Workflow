"""Behavior tests for stable markdown reference integrity."""

from ralph.mcp.artifacts.markdown._document import ParsedItem
from ralph.mcp.artifacts.markdown._references import (
    validate_acyclic_dependencies,
    validate_references,
    validate_unique_ids,
)


def test_duplicate_ids_can_be_case_insensitive() -> None:
    diagnostics = validate_unique_ids(
        [ParsedItem("AC-01", "one", 3, None), ParsedItem("ac-01", "two", 4, None)],
        section="Acceptance Criteria",
        case_sensitive=False,
    )

    assert [(diagnostic.line, diagnostic.rule_id) for diagnostic in diagnostics] == [(4, "REF002")]


def test_reference_and_cycle_errors_name_the_stable_target() -> None:
    dangling = validate_references({"S9": [("S1", 8, "Steps")]}, ["S1"])
    cycle = validate_acyclic_dependencies(
        {"S1": ["S2"], "S2": ["S1"]}, line_by_id={"S1": 3, "S2": 4}
    )

    assert dangling[0].message == "'S1' references unknown ID 'S9'"
    assert cycle[0].line == 3
    assert cycle[0].message == "dependency cycle detected at ID 'S1'"

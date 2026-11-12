from __future__ import annotations

import pytest

from tests.project_policy.kernel_contract import (
    AGENT_RULES,
    RETIRED_PRE_FIX_REASON_SUBSTRINGS,
    SPEC_VALUE_GUARD_NAME,
    agent_rule_statements,
    assert_spec_value_guard_contract,
    enforcer_status_rows,
    evidence_tag,
    guard_inventory_rows,
    kernel,
    mutated_guard_inventory_texts,
    numbered_enforcer_rows,
    root_kernel,
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_kernels_declare_exactly_the_thirteen_agent_rules": (
        "the deleted agent rule is removed by the policy trim, so the kernel "
        "declares thirteen numbered agent rules; the proof module grew past the "
        "repository file-size ceiling, so the check moved to this module with its "
        "assertions unchanged"
    ),
    "test_kernels_number_the_enforcer_ladder_rows_to_match_the_contract": (
        "the deleted agent rule is removed by the policy trim, so the enforcer "
        "ladder carries one numbered row per surviving rule; the proof module grew "
        "past the repository file-size ceiling, so the check moved to this module "
        "with its assertions unchanged"
    ),
    "test_root_kernel_guard_inventory_covers_every_agent_rule": (
        "the deleted agent rule is removed by the policy trim, so the root "
        "guard inventory covers the thirteen surviving agent rules; the proof "
        "module grew past the repository file-size ceiling, so the check moved to "
        "this module with its assertions unchanged"
    ),
    "test_kernel_root_and_starter_agent_rules_carry_the_same_titles_and_evidence_tags": (
        "the deleted agent rule is removed by the policy trim, so both kernels "
        "carry the same thirteen rule titles with the same evidence tags; the "
        "proof module grew past the repository file-size ceiling, so the check "
        "moved to this module with its assertions unchanged"
    ),
    "test_every_advisory_enforcer_status_names_its_review_trigger": (
        "the deleted agent rule is removed by the policy trim, so the advisory "
        "status of every surviving rule carries the review trigger the kernel "
        "contract requires; the proof module grew past the repository file-size "
        "ceiling, so the check moved to this module with its assertions unchanged"
    ),
    "test_kernel_rule_four_enforcement_points_at_the_spec_derived_expected_value": (
        "the deleted agent rule is removed by the policy trim, so the rule 4 "
        "enforcement cells name the spec-derived expected value instead of a "
        "pre-fix failure record; the proof module grew past the repository "
        "file-size ceiling, so the check moved to this module with its assertions "
        "unchanged"
    ),
    "test_advisory_guard_inventory_check_is_fault_sensitive_to_a_dropped_row_and_reason": (
        "the deleted agent rule is removed by the policy trim, so the "
        "fault-sensitivity proof covers the advisory guard row that survived the "
        "trim and still carries an advisory reason and a review trigger; the proof "
        "module grew past the repository file-size ceiling, so the check moved to "
        "this module with its assertions unchanged"
    ),
}


def test_kernels_declare_exactly_the_thirteen_agent_rules() -> None:
    for source_name, content in (("root", root_kernel()), ("starter", kernel())):
        statements = agent_rule_statements(content)
        assert len(statements) == len(AGENT_RULES), (
            f"{source_name} kernel declares {len(statements)} agent rules; the "
            f"shipped contract is {len(AGENT_RULES)}"
        )
        for number, rule in AGENT_RULES:
            assert rule.lower() in statements, (
                f"{source_name} kernel is missing agent rule {number} ({rule!r})"
            )


def test_kernels_number_the_enforcer_ladder_rows_to_match_the_contract() -> None:
    for source_name, content in (("root", root_kernel()), ("starter", kernel())):
        rows = numbered_enforcer_rows(content)
        assert rows == list(AGENT_RULES), (
            f"{source_name} kernel numbers its enforcer-ladder rows {rows}; the shipped "
            f"contract is {list(AGENT_RULES)}"
        )


def test_root_kernel_guard_inventory_covers_every_agent_rule() -> None:
    guards = [row[0] for row in guard_inventory_rows(root_kernel())]
    for number, rule in AGENT_RULES:
        assert guards.count(rule) == 1, (
            f"root kernel guard inventory carries {guards.count(rule)} rows for agent "
            f"rule {number} ({rule!r}); the contract is exactly one"
        )


def test_kernel_root_and_starter_agent_rules_carry_the_same_titles_and_evidence_tags() -> None:
    root_statements = agent_rule_statements(root_kernel())
    starter_statements = agent_rule_statements(kernel())
    for number, rule in AGENT_RULES:
        title = rule.lower()
        assert title in root_statements, f"root kernel is missing agent rule {number} ({rule!r})"
        assert title in starter_statements, (
            f"starter kernel is missing agent rule {number} ({rule!r})"
        )
        root_tag = evidence_tag(root_statements[title])
        starter_tag = evidence_tag(starter_statements[title])
        assert root_tag == starter_tag, (
            f"agent rule {number} ({rule!r}) carries evidence tag {root_tag!r} in the "
            f"root kernel and {starter_tag!r} in the starter kernel"
        )


def test_every_advisory_enforcer_status_names_its_review_trigger() -> None:
    for source_name, content in (("root", root_kernel()), ("starter", kernel())):
        statuses = enforcer_status_rows(content)
        for number, rule in AGENT_RULES:
            status = statuses.get(rule)
            assert status is not None, (
                f"{source_name} kernel rule-to-enforcer ladder has no row for agent "
                f"rule {number} ({rule!r})"
            )
            if not status.startswith("advisory:"):
                continue
            assert "; review:" in status, (
                f"{source_name} kernel agent rule {number} ({rule!r}) keeps an advisory "
                f"enforcement without a review trigger; observed: {status!r}"
            )


def test_kernel_rule_four_enforcement_points_at_the_spec_derived_expected_value() -> None:
    root_enforcement = assert_spec_value_guard_contract(root_kernel(), "root kernel")
    root_status = enforcer_status_rows(root_kernel()).get(SPEC_VALUE_GUARD_NAME)
    starter_status = enforcer_status_rows(kernel()).get(SPEC_VALUE_GUARD_NAME)
    assert root_status is not None, (
        f"root kernel rule-to-enforcer ladder has no row for rule 4 ({SPEC_VALUE_GUARD_NAME!r})"
    )
    assert starter_status is not None, (
        f"starter kernel rule-to-enforcer ladder has no row for rule 4 ({SPEC_VALUE_GUARD_NAME!r})"
    )
    assert root_status == root_enforcement, (
        "root kernel rule-to-enforcer ladder must repeat the guard-inventory "
        f"Enforcement cell verbatim; ladder: {root_status!r}; inventory: "
        f"{root_enforcement!r}"
    )
    assert root_status == starter_status, (
        "root kernel rule 4 enforcement cell differs from the starter kernel cell: "
        f"{root_status!r} != {starter_status!r}"
    )
    for source_name, status in (("root kernel", root_status), ("starter kernel", starter_status)):
        lowered = status.lower()
        for retired in RETIRED_PRE_FIX_REASON_SUBSTRINGS:
            assert retired not in lowered, (
                f"{source_name} rule 4 enforcement still names the retired pre-fix "
                f"failure obligation {retired!r}"
            )


def test_advisory_guard_inventory_check_is_fault_sensitive_to_a_dropped_row_and_reason() -> None:
    without_row, without_reason = mutated_guard_inventory_texts(root_kernel())
    with pytest.raises(AssertionError, match="no row whose Guard cell is exactly"):
        assert_spec_value_guard_contract(without_row, "root kernel without the rule 4 row")
    with pytest.raises(AssertionError, match="does not state the advisory reason"):
        assert_spec_value_guard_contract(without_reason, "root kernel without the reason")

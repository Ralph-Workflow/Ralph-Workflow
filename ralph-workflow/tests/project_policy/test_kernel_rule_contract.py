from __future__ import annotations

import pytest

from tests.project_policy.kernel_contract import (
    AGENT_RULES,
    COMMENT_BAN_CURRENT_FACT_STATEMENTS,
    IDENTICAL_AGENT_RULE_TITLES,
    INVERTED_SMALLEST_CHANGE_OBLIGATION,
    PREMISE_CHECK_ENFORCEMENT,
    PREMISE_CHECK_EVIDENCE_TAG,
    PREMISE_CHECK_RULE_NAME,
    PROJECT_SPECIFIC_AGENT_RULE_TITLES,
    RETIRED_PRE_FIX_REASON_SUBSTRINGS,
    SMALLEST_CHANGE_OBLIGATION,
    SMALLEST_CHANGE_RULE_TITLE,
    SPEC_VALUE_GUARD_NAME,
    STARTER_COMMENT_BAN_ENFORCEMENT,
    agent_rule_body,
    agent_rule_statements,
    assert_spec_value_guard_contract,
    commenting_evidence_paragraph,
    enforcement_cell,
    enforcer_status_rows,
    evidence_tag,
    guard_inventory_rows,
    guard_row,
    kernel,
    mutated_guard_inventory_texts,
    numbered_enforcer_rows,
    root_kernel,
    root_policy,
    starter,
)

COMMENT_BAN_GUARD_NAME: str = "Comment and docstring absence"

COMMENT_BAN_ENFORCEMENT: str = (
    "deterministic: absolute comment and docstring policy audit (audit_comments)"
)

COMMENT_BAN_EVIDENCE_STATEMENT: str = "The ban itself is a project judgment tagged `none`"

COMMENT_BAN_DETERMINISTIC_STATEMENT: str = (
    "The ban ships as `deterministic` because the comment audit enforces it"
)

RETIRED_COMMENT_BAN_STATEMENT: str = (
    "the ban rests on the project rule rather than on a measured effect size"
)

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_kernels_declare_exactly_the_fourteen_agent_rules": (
        "the premise-check rule is stated again as rule 7, so the kernel declares "
        "fourteen numbered agent rules; the kernel contract checks moved into this "
        "module so one module owns the contract, with their assertions unchanged"
    ),
    "test_kernels_number_the_enforcer_ladder_rows_to_match_the_contract": (
        "the premise-check rule is stated again as rule 7, so the enforcer ladder "
        "carries one numbered row per shipped rule; the kernel contract checks moved "
        "into this module so one module owns the contract, with their assertions "
        "unchanged"
    ),
    "test_root_kernel_guard_inventory_covers_every_agent_rule": (
        "the premise-check rule is stated again as rule 7, so the root guard "
        "inventory covers the fourteen shipped agent rules; the kernel contract checks "
        "moved into this module so one module owns the contract, with their "
        "assertions unchanged"
    ),
    "test_kernel_root_and_starter_agent_rules_carry_the_same_titles_and_evidence_tags": (
        "the premise-check rule is stated again as rule 7, so both kernels carry "
        "the same fourteen rule titles with the same evidence tags; the kernel "
        "contract checks moved into this module so one module owns the contract, "
        "with their assertions unchanged"
    ),
    "test_every_advisory_enforcer_status_names_its_review_trigger": (
        "the premise-check rule is stated again as rule 7, so the advisory status "
        "of every shipped rule carries the review trigger the kernel contract "
        "requires; the kernel contract checks moved into this module so one module "
        "owns the contract, with their assertions unchanged"
    ),
    "test_kernel_rule_four_enforcement_points_at_the_spec_derived_expected_value": (
        "the premise-check rule is stated again as rule 7, so the rule 4 "
        "enforcement cells name the spec-derived expected value instead of a "
        "pre-fix failure record; the kernel contract checks moved into this module so "
        "one module owns the contract, with their assertions unchanged"
    ),
    "test_advisory_guard_inventory_check_is_fault_sensitive_to_a_dropped_row_and_reason": (
        "the premise-check rule is stated again as rule 7, so the "
        "fault-sensitivity proof covers the advisory guard row that survived the "
        "trim and still carries an advisory reason and a review trigger; the kernel "
        "contract checks moved into this module so one module owns the contract, "
        "with their assertions unchanged"
    ),
    "test_kernels_state_the_premise_check_rule_with_its_advisory_enforcement": (
        "the premise-check rule is stated again as rule 7 with no resolving "
        "deterministic enforcer, so both kernels carry it with the same advisory "
        "enforcement cell in the ladder and in the guard inventory"
    ),
    "test_clean_code_policy_states_the_comment_ban_evidence_tag_and_enforcement": (
        "the comment ban is a `none`-tagged project rule that a deterministic gate "
        "decides, so the clean-code policy states that evidence tag and its "
        "deterministic enforcement next to the guard row that carries the audit"
    ),
    "test_clean_code_starter_and_root_state_the_same_comment_ban_current_facts": (
        "the starter clean-code policy shipped the retired sentence that stated the "
        "comment ban without its evidence tag, so the starter and the root pack are "
        "now compared on the current-fact statements of the comment-ban paragraph"
    ),
    "test_kernel_root_and_starter_carry_the_same_rule_text_for_the_shared_agent_rules": (
        "the starter kernel inverted the smallest-change obligation while the rule "
        "list was reflowed, so the rules whose text is not project-specific are "
        "compared verbatim between the root kernel and the starter kernel"
    ),
    "test_the_shared_and_project_specific_agent_rule_titles_cover_every_agent_rule": (
        "the shared and the project-specific agent-rule sets are named explicitly, so "
        "the parity check cannot silently skip a rule when the rule list changes"
    ),
    "test_smallest_change_requires_deleting_the_replaced_api_in_both_kernels": (
        "the smallest-change obligation was inverted in one pack, so both packs are "
        "checked for the requirement that deletes the replaced API and for the "
        "absence of the inverted wording"
    ),
}


def test_kernels_declare_exactly_the_fourteen_agent_rules() -> None:
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


def test_kernels_state_the_premise_check_rule_with_its_advisory_enforcement() -> None:
    for source_name, content in (("root", root_kernel()), ("starter", kernel())):
        statements = agent_rule_statements(content)
        statement = statements.get(PREMISE_CHECK_RULE_NAME.lower())
        assert statement is not None, (
            f"{source_name} kernel does not state the {PREMISE_CHECK_RULE_NAME!r} agent rule"
        )
        assert evidence_tag(statement) == PREMISE_CHECK_EVIDENCE_TAG, (
            f"{source_name} kernel rule {PREMISE_CHECK_RULE_NAME!r} carries evidence tag "
            f"{evidence_tag(statement)!r}; the shipped contract is "
            f"{PREMISE_CHECK_EVIDENCE_TAG!r}"
        )
        status = enforcer_status_rows(content).get(PREMISE_CHECK_RULE_NAME)
        assert status == PREMISE_CHECK_ENFORCEMENT, (
            f"{source_name} kernel rule {PREMISE_CHECK_RULE_NAME!r} carries enforcement "
            f"{status!r}; the shipped contract is {PREMISE_CHECK_ENFORCEMENT!r}"
        )
        row = guard_row(content, PREMISE_CHECK_RULE_NAME, f"{source_name} kernel")
        assert enforcement_cell(row) == PREMISE_CHECK_ENFORCEMENT, (
            f"{source_name} kernel guard-inventory row for {PREMISE_CHECK_RULE_NAME!r} "
            f"carries enforcement {enforcement_cell(row)!r}; the ladder cell and the "
            f"inventory cell must match {PREMISE_CHECK_ENFORCEMENT!r}"
        )


def test_clean_code_policy_states_the_comment_ban_evidence_tag_and_enforcement() -> None:
    content = root_policy("clean-code-policy.md")
    normalized = " ".join(content.split())
    assert COMMENT_BAN_EVIDENCE_STATEMENT in normalized, (
        "clean-code policy does not state that the comment ban is a project judgment "
        f"tagged `none`; expected: {COMMENT_BAN_EVIDENCE_STATEMENT!r}"
    )
    assert COMMENT_BAN_DETERMINISTIC_STATEMENT in normalized, (
        "clean-code policy does not state that the comment ban ships as "
        f"`deterministic`; expected: {COMMENT_BAN_DETERMINISTIC_STATEMENT!r}"
    )
    assert RETIRED_COMMENT_BAN_STATEMENT not in normalized, (
        "clean-code policy still carries the sentence that states the ban without its "
        f"evidence tag: {RETIRED_COMMENT_BAN_STATEMENT!r}"
    )
    row = guard_row(content, COMMENT_BAN_GUARD_NAME, "root clean-code policy")
    assert enforcement_cell(row) == COMMENT_BAN_ENFORCEMENT, (
        "clean-code policy guard-inventory row for the comment ban carries enforcement "
        f"{enforcement_cell(row)!r}; the shipped contract is {COMMENT_BAN_ENFORCEMENT!r}"
    )


def test_clean_code_starter_and_root_state_the_same_comment_ban_current_facts() -> None:
    root_paragraph = commenting_evidence_paragraph(root_policy("clean-code-policy.md"))
    starter_paragraph = commenting_evidence_paragraph(starter("clean-code-policy.md"))
    for statement in COMMENT_BAN_CURRENT_FACT_STATEMENTS:
        assert statement in root_paragraph, (
            f"root clean-code policy does not state the comment-ban fact {statement!r}"
        )
        assert statement in starter_paragraph, (
            "starter clean-code policy does not state the comment-ban fact "
            f"{statement!r}; a fresh project must ship the same current facts as the "
            "root pack"
        )
    assert RETIRED_COMMENT_BAN_STATEMENT not in root_paragraph, (
        "root clean-code policy still carries the retired sentence that states the "
        f"comment ban without its evidence tag: {RETIRED_COMMENT_BAN_STATEMENT!r}"
    )
    assert RETIRED_COMMENT_BAN_STATEMENT not in starter_paragraph, (
        "starter clean-code policy still carries the retired sentence that states the "
        f"comment ban without its evidence tag: {RETIRED_COMMENT_BAN_STATEMENT!r}"
    )
    row = guard_row(
        starter("clean-code-policy.md"),
        COMMENT_BAN_GUARD_NAME,
        "starter clean-code policy",
    )
    assert enforcement_cell(row) == STARTER_COMMENT_BAN_ENFORCEMENT, (
        "starter clean-code policy guard-inventory row for the comment ban carries "
        f"enforcement {enforcement_cell(row)!r}; the starter keeps its unresolved "
        f"enforcer placeholder {STARTER_COMMENT_BAN_ENFORCEMENT!r}"
    )


def test_the_shared_and_project_specific_agent_rule_titles_cover_every_agent_rule() -> None:
    shared = {title.lower() for title in IDENTICAL_AGENT_RULE_TITLES}
    project_specific = {title.lower() for title in PROJECT_SPECIFIC_AGENT_RULE_TITLES}
    contract = {title.lower() for _number, title in AGENT_RULES}
    assert not shared & project_specific, (
        f"agent rules {sorted(shared & project_specific)} are named as both shared and "
        "project-specific"
    )
    assert shared | project_specific == contract, (
        f"the shared and project-specific agent-rule titles cover "
        f"{sorted(shared | project_specific)}; the shipped contract is "
        f"{sorted(contract)}"
    )


def test_kernel_root_and_starter_carry_the_same_rule_text_for_the_shared_agent_rules() -> None:
    for title in IDENTICAL_AGENT_RULE_TITLES:
        root_body = agent_rule_body(root_kernel(), title)
        starter_body = agent_rule_body(kernel(), title)
        assert root_body == starter_body, (
            f"agent rule {title!r} differs between the root kernel and the starter "
            "kernel; a rule whose text is not project-specific must carry the same "
            f"text in both packs:\nroot:    {root_body!r}\nstarter: {starter_body!r}"
        )


def test_smallest_change_requires_deleting_the_replaced_api_in_both_kernels() -> None:
    for source_name, content in (("root", root_kernel()), ("starter", kernel())):
        body = agent_rule_body(content, SMALLEST_CHANGE_RULE_TITLE)
        assert SMALLEST_CHANGE_OBLIGATION in body, (
            f"{source_name} kernel rule 8 does not require deleting the replaced API "
            f"in the same change; expected: {SMALLEST_CHANGE_OBLIGATION!r}; observed: "
            f"{body!r}"
        )
        assert INVERTED_SMALLEST_CHANGE_OBLIGATION not in body, (
            f"{source_name} kernel rule 8 inverts the requirement: it deletes the "
            "replacement instead of the replaced API; observed: "
            f"{body!r}"
        )

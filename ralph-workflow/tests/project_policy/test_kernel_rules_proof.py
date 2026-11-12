from __future__ import annotations

import re
from pathlib import Path

import pytest

from ralph.project_policy import markers, starters

RALPH_TEST_CHANGE_REASONS = {
    "test_kernels_declare_exactly_the_fourteen_agent_rules": (
        "the retired contract froze the shipped kernel at twelve agent rules; the "
        "premise rule is re-landed as numbered rule 7 and the non-inferable "
        "instruction-surface rule is added as numbered rule 14, so the kernel "
        "declares fourteen agent rules"
    ),
    "test_kernels_state_the_premise_check_rule": (
        "the retired contract asserted the kernel must not state the premise rule; "
        "the premise rule is re-landed, so the kernel must state it with its "
        "anecdotal evidence tag and its advisory enforcement with a review trigger"
    ),
    "test_kernel_starter_and_root_rule_to_phase_table_covers_all_numbered_rules": (
        "the retired contract required the rule-to-phase table to cover twelve "
        "numbered rules; the re-landed premise rule and the new instruction-surface "
        "rule make the table carry fourteen numbered rules"
    ),
    "test_kernels_number_the_agent_rules_to_match_the_contract": (
        "the retired contract compared the kernel rule count against a twelve-rule "
        "list; the re-landed premise rule and the new instruction-surface rule make "
        "the contract fourteen numbered rules"
    ),
}

KERNEL_RULES: tuple[str, ...] = (
    "truthful evidence",
    "run every gate",
    "no weakening",
    "policy follows reality",
    "report failures honestly",
)

KERNEL_RULE_SUBSTANTIVE_OBLIGATIONS: dict[str, tuple[str, ...]] = {
    "truthful evidence": ("must be backed by a record",),
    "run every gate": (
        "run every",
        "report the actual outcome",
    ),
    "no weakening": (
        "never the gate",
        "violation",
    ),
    "policy follows reality": ("update policy",),
    "report failures honestly": ("report the failure honestly",),
}

KERNEL_RULE_RETIREMENT_PHRASES: tuple[str, ...] = (
    "retired",
    "deprecated",
    "no longer required",
    "optional",
)

LOAD_ORDER_STATEMENT = "Load order: kernel → role slice → triggered domain file → stop."

LOAD_STEP_SLICE_MARKER = "LOAD only the rule slice"
LOAD_STEP_DOMAIN_MARKER = "LOAD only the domain polic"

BRIEF_EVIDENCE_TAGS: dict[str, str] = {
    "Regression tests from the spec": "moderate",
    "Smallest change": "anecdotal",
    "Context hygiene": "strong",
}

DETERMINISTIC_TAG_ANCHOR = "Only a rule tagged `strong` or `moderate` may carry a `deterministic`"

AGENT_RULES: tuple[tuple[int, str], ...] = (
    (1, "Check first"),
    (2, "Real-artifact evidence"),
    (3, "Test integrity"),
    (4, "Regression tests from the spec"),
    (5, "Independent review"),
    (6, "Handoff by artifact"),
    (7, "Premise check"),
    (8, "Smallest change"),
    (9, "Context hygiene"),
    (10, "Plan scaling"),
    (11, "Commit-level hygiene"),
    (12, "Human checkpoints"),
    (13, "Rule-to-enforcer ladder"),
    (14, "Non-inferable instruction surface"),
)

INSTRUCTION_AUDIT_TRIGGER = "A model or harness generation change"
INSTRUCTION_AUDIT_REQUIREMENTS: tuple[str, ...] = (
    "re-audit",
    "stale",
    "dead-path",
    "contradictory",
)

EVIDENCE_TAG_PATTERN = re.compile(r"\(evidence:\s*(strong|moderate|anecdotal|none)\)")


def _starter(name: str) -> str:
    return starters.read_starter(name)


def _kernel() -> str:
    return starters.read_starter("execution-kernel.md")


def _root_kernel() -> str:
    path = (
        Path(__file__).resolve().parents[3]
        / "docs"
        / "ralph-workflow-policy"
        / "execution-kernel.md"
    )
    assert path.is_file(), f"root kernel file not found at {path}"
    return path.read_text(encoding="utf-8")


def _parse_inventory(name: str) -> list[dict[str, str]]:
    content = _starter(name)
    lines = content.splitlines()
    try:
        start = lines.index(markers.GUARD_INVENTORY_HEADING)
    except ValueError:
        return []
    table_lines = [line.strip() for line in lines[start + 1 :] if line.strip().startswith("|")]
    if len(table_lines) < 3:
        return []
    headers = [cell.strip().lower().replace(" ", "_") for cell in table_lines[0].split("|")[1:-1]]
    rows: list[dict[str, str]] = []
    for line in table_lines[2:]:
        cells = [cell.strip() for cell in line.split("|")[1:-1]]
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells, strict=False)))
    return rows


def _extract_kernel_rules_section(content: str) -> str:
    heading = "## Kernel rules"
    assert heading in content, f"kernel starter missing {heading!r} section"
    start = content.index(heading) + len(heading)
    rest = content[start:]
    next_heading = rest.find("\n## ")
    return rest[:next_heading] if next_heading != -1 else rest


def _extract_rule_statements(section: str) -> dict[str, str]:
    pattern = re.compile(r"((?:\d+\.|\*)\s+\*\*(.*?)\*\*[\s\S]*?)(?=\n(?:\d+\.|\*)\s+\*\*|\Z)")
    rules: dict[str, str] = {}
    for match in pattern.finditer(section):
        block: str = match.group(1)
        raw_title: str = match.group(2)
        title: str = raw_title.rstrip(".:").strip().lower()
        rules[title] = " ".join(block.split())
    return rules


def _verify_rule_statement(
    rule_name: str,
    statement: str,
    obligations: tuple[str, ...],
    retirement_phrases: tuple[str, ...] = KERNEL_RULE_RETIREMENT_PHRASES,
) -> None:
    normalized = statement.lower()
    for obligation in obligations:
        assert obligation.lower() in normalized, (
            f"Rule {rule_name!r} missing substantive mandatory obligation: {obligation!r}"
        )
    for phrase in retirement_phrases:
        assert phrase.lower() not in normalized, (
            f"Rule {rule_name!r} statement contains prohibited retirement phrase: {phrase!r}"
        )


def test_kernel_starter_carries_all_five_rules() -> None:
    content = _kernel()
    section = _extract_kernel_rules_section(content)
    rules = _extract_rule_statements(section)

    for rule in KERNEL_RULES:
        assert rule in rules, (
            f"## Kernel rules section missing the {rule!r} rule; the "
            f"five-rule contract is the v5 deliverable"
        )
        statement = rules[rule]
        obligations = KERNEL_RULE_SUBSTANTIVE_OBLIGATIONS.get(rule, ())
        _verify_rule_statement(rule, statement, obligations)


def test_kernel_rules_proof_is_fault_sensitive_to_missing_rule() -> None:
    content = _kernel()
    section = _extract_kernel_rules_section(content)
    rules = _extract_rule_statements(section)
    for rule in KERNEL_RULES:
        mutated_rules = {k: v for k, v in rules.items() if k != rule}
        with pytest.raises(AssertionError, match="missing the .* rule"):
            for r in KERNEL_RULES:
                assert r in mutated_rules, f"## Kernel rules section missing the {r!r} rule"


def test_kernel_rules_proof_is_fault_sensitive_to_stripped_obligation() -> None:
    for rule, obligations in KERNEL_RULE_SUBSTANTIVE_OBLIGATIONS.items():
        hollow_statement = f"1. **{rule.title()}.** Generic filler without substantive obligation."
        with pytest.raises(AssertionError, match="missing substantive mandatory obligation"):
            _verify_rule_statement(rule, hollow_statement, obligations)


@pytest.mark.parametrize("retirement_phrase", KERNEL_RULE_RETIREMENT_PHRASES)
def test_kernel_rules_proof_is_fault_sensitive_to_retirement_phrases(
    retirement_phrase: str,
) -> None:
    content = _kernel()
    section = _extract_kernel_rules_section(content)
    rules = _extract_rule_statements(section)
    for rule in KERNEL_RULES:
        statement = rules[rule]
        mutated_statement = f"{statement} Note: this rule is {retirement_phrase}."
        obligations = KERNEL_RULE_SUBSTANTIVE_OBLIGATIONS.get(rule, ())
        with pytest.raises(AssertionError, match="prohibited retirement phrase"):
            _verify_rule_statement(rule, mutated_statement, obligations)


def test_kernel_starter_declares_precedence_over_domain_policies() -> None:
    content = _kernel()
    normalized = " ".join(content.split()).lower()
    assert "precedence" in normalized
    assert "kernel" in normalized


def test_kernel_starter_declares_selective_applicability() -> None:
    content = _kernel()
    normalized = " ".join(content.split()).lower()
    assert "selective" in normalized or "trigger" in normalized


def test_kernel_starter_declares_claim_scope_rule() -> None:
    content = _kernel()
    normalized = " ".join(content.split()).lower()
    has_claim_scope = "claim-scope" in normalized or "claim scope" in normalized
    has_broad_phrase = any(phrase.lower() in normalized for phrase in markers.CLAIM_SCOPE_PHRASES)
    assert has_claim_scope or has_broad_phrase, (
        "kernel starter does not name the claim-scope rule; the v5 B7 anchor is missing"
    )


def test_a2_security_protection_in_security_starter() -> None:
    content = _starter("security-policy.md")
    normalized = " ".join(content.split()).lower()
    assert "security" in normalized
    rows = _parse_inventory("security-policy.md")
    security_rows = [row for row in rows if "security" in row["protected_outcome"].lower()]
    assert security_rows, (
        "security starter guard-inventory missing a row whose "
        "Protected outcome names security; A2 protection (1) "
        "has no guard"
    )


def test_a2_authorization_protection_in_security_starter() -> None:
    content = _starter("security-policy.md")
    normalized = " ".join(content.split()).lower()
    assert "authorization" in normalized or "tenant" in normalized or "scope" in normalized, (
        "security starter missing authorization / tenant / scope wording"
    )
    rows = _parse_inventory("security-policy.md")
    auth_rows = [
        row
        for row in rows
        if any(
            keyword in row["protected_outcome"].lower()
            for keyword in ("authorization", "tenant", "scope")
        )
    ]
    assert auth_rows, (
        "security starter guard-inventory missing an "
        "authorization / tenant / scope row; A2 protection (2) "
        "has no guard"
    )


def test_a2_destructive_operations_protection_in_security_starter() -> None:
    content = _starter("security-policy.md")
    normalized = " ".join(content.split()).lower()
    assert "destructive" in normalized
    rows = _parse_inventory("security-policy.md")
    destructive_rows = [row for row in rows if "destructive" in row["protected_outcome"].lower()]
    assert destructive_rows, (
        "security starter guard-inventory missing a destructive row; A2 protection (3) has no guard"
    )


def test_a2_process_resource_ownership_in_architecture_starter() -> None:
    content = _starter("architecture-policy.md")
    normalized = " ".join(content.split()).lower()
    assert "process" in normalized or "resource" in normalized
    rows = _parse_inventory("architecture-policy.md")
    pr_rows = [
        row
        for row in rows
        if any(keyword in row["protected_outcome"].lower() for keyword in ("process", "resource"))
    ]
    assert pr_rows, (
        "architecture starter guard-inventory missing a process "
        "or resource row; A2 protection (4) has no guard"
    )


def test_a2_deterministic_seams_in_testing_starter() -> None:
    content = _starter("testing-policy.md")
    normalized = " ".join(content.split()).lower()
    seam_words = ("clock", "network", "filesystem", "process", "dns")
    missing = [w for w in seam_words if w not in normalized]
    assert not missing, (
        f"testing starter does not name every deterministic seam; "
        f"missing: {missing!r}; A2 protection (5) requires all five"
    )
    rows = _parse_inventory("testing-policy.md")
    seam_rows = [
        row for row in rows if all(w in row["protected_outcome"].lower() for w in seam_words)
    ]
    assert seam_rows, (
        "testing starter guard-inventory missing a row whose "
        "Protected outcome names every deterministic seam (clock, "
        "network, filesystem, process, DNS); A2 protection (5) "
        "has no guard"
    )


def test_a2_schema_data_integrity_in_data_storage_starter() -> None:
    content = _starter("data-storage-policy.md")
    normalized = " ".join(content.split()).lower()
    assert "schema" in normalized or "data integrity" in normalized
    rows = _parse_inventory("data-storage-policy.md")
    schema_rows = [
        row
        for row in rows
        if any(
            keyword in row["protected_outcome"].lower()
            for keyword in ("schema", "data integrity", "migration")
        )
    ]
    assert schema_rows, (
        "data-storage starter guard-inventory missing a schema / "
        "data-integrity row; A2 protection (6) has no guard"
    )


def test_domain_starters_deduplicate_living_document_contract_to_kernel_pointer() -> None:
    domain_starters = [
        name
        for name in starters.STARTER_NAMES
        if name != "execution-kernel.md" and name.endswith(".md")
    ]
    assert len(domain_starters) >= 20, "Expected at least 20 domain starters"

    generic_weakened_phrase = "weakened to obtain a passing result"
    pointer_sentence = (
        "Shared amendment admission and retention are governed once by "
        "`policy-portfolio.toml`; generic rule protection and non-weakening are "
        "governed by `execution-kernel.md §Kernel rules`."
    )

    for name in domain_starters:
        content = _starter(name)
        lower_content = " ".join(content.split()).lower()
        normalized_content = " ".join(content.split())

        assert generic_weakened_phrase not in lower_content, (
            f"{name} duplicates the generic kernel rule phrase "
            f"{generic_weakened_phrase!r} in its body; B10 requires pointing "
            f"to execution-kernel.md instead"
        )

        assert "execution-kernel.md" in normalized_content, (
            f"{name} missing execution-kernel.md pointer"
        )
        assert pointer_sentence in normalized_content, (
            f"{name} missing canonical living document kernel pointer sentence: "
            f"{pointer_sentence!r}"
        )

    kernel_content = _kernel()
    normalized_kernel = " ".join(kernel_content.split()).lower()
    assert generic_weakened_phrase in normalized_kernel, (
        "execution-kernel.md must retain the canonical non-weakening rule"
    )


KERNEL_RULE_PARAPHRASE_PATTERNS: tuple[str, ...] = (
    "i think it works",
    "actual command and outcome",
    "report the actual command",
    "report the actual outcome",
    "report failures accurately",
    "obtain a passing result",
    "obtaining a passing result",
    "weaken a check to obtain",
    "weakening a check to obtain",
    "avoid unsupported claims",
    "must not make unsupported claims",
)


def _body_sans_kernel_pointer_sections(content: str) -> str:
    excluded = (
        "## AI execution instructions",
        "## Living document contract",
    )
    lines = content.splitlines(keepends=True)
    keep: list[str] = []
    skipping = False
    for line in lines:
        if line.startswith("## "):
            skipping = any(line.startswith(s) for s in excluded)
            if skipping:
                continue
        if not skipping:
            keep.append(line)
    return "".join(keep)


def test_domain_starters_do_not_paraphrase_kernel_rules_in_body() -> None:
    domain_starters = [
        name
        for name in starters.STARTER_NAMES
        if name != "execution-kernel.md" and name.endswith(".md")
    ]
    assert len(domain_starters) >= 20, "Expected at least 20 domain starters"

    for name in domain_starters:
        content = _starter(name)
        body = _body_sans_kernel_pointer_sections(content)
        lower_body = " ".join(body.split()).lower()
        for pattern in KERNEL_RULE_PARAPHRASE_PATTERNS:
            assert pattern not in lower_body, (
                f"{name} paraphrases a kernel rule in its body prose "
                f"with {pattern!r}; B10 requires the five generic "
                f"rules live only in execution-kernel.md and be loaded "
                f"by reference from the AI execution instructions "
                f"section, not re-derived in starter body prose"
            )


def test_kernel_starter_and_root_rule_names_parity() -> None:
    starter_rules = _extract_rule_statements(_extract_kernel_rules_section(_kernel()))
    root_rules = _extract_rule_statements(_extract_kernel_rules_section(_root_kernel()))

    assert set(starter_rules.keys()) == set(KERNEL_RULES), (
        f"starter rules {set(starter_rules.keys())} do not match expected {set(KERNEL_RULES)}"
    )
    assert set(root_rules.keys()) == set(KERNEL_RULES), (
        f"root rules {set(root_rules.keys())} do not match expected {set(KERNEL_RULES)}"
    )


def test_kernel_starter_and_root_step_3_names() -> None:
    step_3_pattern = re.compile(
        r"3\.\s+ENFORCE the five kernel rules[^\n]*\n(?:[ \t]+[^\n]*\n)*",
        re.MULTILINE,
    )

    for source_name, content in (("starter", _kernel()), ("root", _root_kernel())):
        match = step_3_pattern.search(content)
        assert match is not None, (
            f"{source_name} execution-kernel.md missing step 3 in AI execution instructions"
        )
        step_3_text = " ".join(match.group(0).split()).lower()
        for rule in KERNEL_RULES:
            assert rule in step_3_text, (
                f"{source_name} step 3 does not name kernel rule {rule!r}: {step_3_text!r}"
            )


def test_kernel_starter_and_root_rule_to_phase_table_covers_all_numbered_rules() -> None:
    table_heading = "## Rule-to-phase table"
    for source_name, content in (("starter", _kernel()), ("root", _root_kernel())):
        assert table_heading in content, f"{source_name} missing {table_heading!r}"
        start = content.index(table_heading) + len(table_heading)
        rest = content[start:]
        next_heading = rest.find("\n## ")
        section = rest[:next_heading] if next_heading != -1 else rest

        found_rules: set[int] = set()
        for num in range(1, len(AGENT_RULES) + 1):
            pattern = re.compile(rf"\({num}\)")
            if pattern.search(section):
                found_rules.add(num)

        missing = set(range(1, len(AGENT_RULES) + 1)) - found_rules
        assert not missing, (
            f"{source_name} rule-to-phase table is missing numbered rule(s): {sorted(missing)}"
        )


def _normalized(text: str) -> str:
    return " ".join(text.split())


def _section(content: str, heading: str) -> str:
    marker = f"\n{heading}\n"
    assert marker in content, f"missing {heading!r} section"
    start = content.index(marker) + len(marker)
    rest = content[start:]
    next_heading = rest.find("\n## ")
    return rest[:next_heading] if next_heading != -1 else rest


def _rule_body(statement: str, rule: str) -> str:
    index = statement.lower().find(f"**{rule.lower()}")
    assert index != -1, f"rule {rule!r} has no bold title in its statement"
    body = statement[index + len(rule) + 2 :]
    return _normalized(body.lstrip(".:").lstrip("*").strip())


def _agent_rule_statements(content: str) -> dict[str, str]:
    return _extract_rule_statements(_section(content, "## Agent rules"))


def _evidence_tag(statement: str) -> str:
    match = EVIDENCE_TAG_PATTERN.search(statement)
    assert match is not None, "rule statement carries no evidence tag"
    return match.group(1)


def _research_basis_blocks(content: str) -> list[str]:
    section = _section(content, "## Research basis")
    blocks: list[str] = []
    current: list[str] = []
    for line in section.splitlines():
        if line.lstrip().startswith("* publisher:"):
            if current:
                blocks.append("\n".join(current))
            current = [line]
            continue
        if current:
            current.append(line)
    if current:
        blocks.append("\n".join(current))
    return blocks


def _supports_text(block: str) -> str:
    collected: list[str] = []
    collecting = False
    for raw_line in block.splitlines():
        stripped = raw_line.strip()
        if stripped.startswith("supports:"):
            collecting = True
            collected.append(stripped[len("supports:") :])
            continue
        if not collecting:
            continue
        if stripped.startswith(("publisher:", "title:", "http:", "review date:", "path:")):
            break
        collected.append(stripped)
    return _normalized(" ".join(collected))


def test_kernel_root_and_starter_carry_the_same_five_rule_bodies() -> None:
    root_rules = _extract_rule_statements(_extract_kernel_rules_section(_root_kernel()))
    starter_rules = _extract_rule_statements(_extract_kernel_rules_section(_kernel()))

    assert set(root_rules.keys()) == set(KERNEL_RULES), (
        f"root rules {sorted(root_rules.keys())} do not match expected {sorted(KERNEL_RULES)}"
    )
    assert set(starter_rules.keys()) == set(KERNEL_RULES), (
        f"starter rules {sorted(starter_rules.keys())} do not match expected {sorted(KERNEL_RULES)}"
    )

    for rule in KERNEL_RULES:
        root_body = _rule_body(root_rules[rule], rule)
        starter_body = _rule_body(starter_rules[rule], rule)
        assert root_body == starter_body, (
            f"kernel rule {rule!r} differs between the root pack and the starter:\n"
            f"root:    {root_body!r}\nstarter: {starter_body!r}"
        )


def test_kernel_root_and_starter_state_the_same_load_order() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _kernel())):
        assert content.count(LOAD_ORDER_STATEMENT) == 1, (
            f"{source_name} kernel must state the load order exactly once "
            f"({LOAD_ORDER_STATEMENT!r}); found {content.count(LOAD_ORDER_STATEMENT)}"
        )
        assert "kernel → domain file" not in _normalized(content), (
            f"{source_name} kernel still states a load order that skips the role slice"
        )


def test_kernel_load_steps_read_the_role_slice_before_the_domain_file() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _kernel())):
        section = _section(content, "## AI execution instructions")
        slice_at = section.find(LOAD_STEP_SLICE_MARKER)
        domain_at = section.find(LOAD_STEP_DOMAIN_MARKER)
        assert slice_at != -1, (
            f"{source_name} AI execution instructions name no role-slice load step"
        )
        assert domain_at != -1, (
            f"{source_name} AI execution instructions name no triggered-domain load step"
        )
        assert slice_at < domain_at, (
            f"{source_name} AI execution instructions load the triggered domain "
            f"file before the role slice; the loading direction is kernel, role "
            f"slice, triggered domain file"
        )


def test_kernel_evidence_tags_match_the_brief() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _kernel())):
        statements = _agent_rule_statements(content)
        for rule, tag in BRIEF_EVIDENCE_TAGS.items():
            statement = statements.get(rule.lower())
            assert statement is not None, f"{source_name} kernel missing agent rule {rule!r}"
            actual = _evidence_tag(statement)
            assert actual == tag, (
                f"{source_name} kernel rule {rule!r} carries evidence tag {actual!r}; "
                f"the brief assigns {tag!r}"
            )


def test_kernel_anchors_the_deterministic_evidence_rule() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _kernel())):
        assert DETERMINISTIC_TAG_ANCHOR in content, (
            f"{source_name} kernel does not anchor the rule that only a `strong` "
            f"or `moderate` rule may carry a `deterministic` enforcement status"
        )


def test_every_agent_rule_has_a_research_basis_supports_line() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _kernel())):
        blocks = _research_basis_blocks(content)
        assert blocks, f"{source_name} kernel research basis has no citation blocks"
        for number, rule in AGENT_RULES:
            matching = [block for block in blocks if rule.lower() in _supports_text(block).lower()]
            assert matching, (
                f"{source_name} kernel research basis has no supports line naming "
                f"rule {number} ({rule!r})"
            )
            assert any(f"({number})" in _supports_text(block) for block in matching), (
                f"{source_name} kernel research basis supports line for rule "
                f"{number} ({rule!r}) does not name its number"
            )


def test_instruction_audit_requirement_is_stated_once_in_both_kernels() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _kernel())):
        assert content.count(INSTRUCTION_AUDIT_TRIGGER) == 1, (
            f"{source_name} kernel must state the instruction-audit requirement exactly "
            f"once; found {content.count(INSTRUCTION_AUDIT_TRIGGER)}"
        )
        normalized = _normalized(content).lower()
        for requirement in INSTRUCTION_AUDIT_REQUIREMENTS:
            assert requirement in normalized, (
                f"{source_name} kernel instruction-audit requirement is missing {requirement!r}"
            )


SKILL_TECHNIQUE_PRECEDENCE = "the kernel (policy) wins"

LIBRARY_DOCUMENTATION_CLAUSE = "that library's own documentation"


def test_kernels_declare_exactly_the_fourteen_agent_rules() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _kernel())):
        statements = _agent_rule_statements(content)
        assert len(statements) == len(AGENT_RULES), (
            f"{source_name} kernel declares {len(statements)} agent rules; the "
            f"shipped contract is {len(AGENT_RULES)}"
        )
        for number, rule in AGENT_RULES:
            assert rule.lower() in statements, (
                f"{source_name} kernel is missing agent rule {number} ({rule!r})"
            )


def _numbered_agent_rules(content: str) -> list[tuple[int, str]]:
    section = _section(content, "## Agent rules")
    pattern = re.compile(r"(\d+)\.\s+\*\*(.*?)\*\*")
    numbered: list[tuple[int, str]] = []
    for match in pattern.finditer(section):
        numbered.append((int(match.group(1)), match.group(2).rstrip(".:").strip()))
    return numbered


def test_kernels_number_the_agent_rules_to_match_the_contract() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _kernel())):
        numbered = _numbered_agent_rules(content)
        assert numbered == list(AGENT_RULES), (
            f"{source_name} kernel numbers its agent rules {numbered}; the shipped "
            f"contract is {list(AGENT_RULES)}"
        )


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


def test_kernels_state_the_premise_check_rule() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _kernel())):
        statements = _agent_rule_statements(content)
        statement = statements.get("premise check")
        assert statement is not None, (
            f"{source_name} kernel does not state the premise-check agent rule"
        )
        assert _evidence_tag(statement) == "anecdotal", (
            f"{source_name} kernel premise-check rule carries evidence tag "
            f"{_evidence_tag(statement)!r}; the rule rests on practitioner "
            f"experience, so the tag is 'anecdotal'"
        )
        status = _enforcer_status_rows(content).get("Premise check")
        assert status is not None, (
            f"{source_name} kernel rule-to-enforcer ladder has no row for the premise-check rule"
        )
        assert status.startswith("advisory:"), (
            f"{source_name} kernel premise-check rule must keep its advisory "
            f"enforcement; observed: {status!r}"
        )
        assert "; review:" in status, (
            f"{source_name} kernel premise-check rule must name its review trigger; "
            f"observed: {status!r}"
        )


def test_kernels_state_that_a_shipped_skill_technique_wins_on_conflict() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _kernel())):
        normalized = _normalized(content)
        assert normalized.count(SKILL_TECHNIQUE_PRECEDENCE) == 1, (
            f"{source_name} kernel must state the shipped-skill precedence line exactly "
            f"once; found {normalized.count(SKILL_TECHNIQUE_PRECEDENCE)}"
        )


def test_kernels_point_a_third_party_behaviour_at_its_own_documentation() -> None:
    for source_name, content in (("root", _root_kernel()), ("starter", _kernel())):
        normalized = _normalized(content)
        assert normalized.count(LIBRARY_DOCUMENTATION_CLAUSE) == 1, (
            f"{source_name} kernel must state the library-documentation clause exactly "
            f"once; found {normalized.count(LIBRARY_DOCUMENTATION_CLAUSE)}"
        )


PREMISE_GUARD_NAME: str = "Premise check"

GUARD_INVENTORY_COLUMNS: tuple[str, ...] = (
    "Guard",
    "Owner",
    "Protected outcome",
    "Detected condition",
    "Claim scope",
    "Enforcement",
    "Retirement trigger",
)

PREMISE_ADVISORY_REASON_SUBSTRINGS: tuple[str, ...] = (
    "formatter is deterministic",
    "evidence is practitioner strength",
)

PREMISE_REVIEW_TRIGGER_SUBSTRINGS: tuple[str, ...] = (
    "retry contract",
    "failure classifier",
)


def _guard_inventory_table_cells(line: str) -> list[str]:
    stripped = line.strip()
    if not stripped.startswith("|"):
        return []
    return [cell.strip() for cell in stripped.split("|")[1:-1]]


def _guard_inventory_rows(content: str) -> list[list[str]]:
    section = _section(content, markers.GUARD_INVENTORY_HEADING)
    table_rows: list[list[str]] = []
    for line in section.splitlines():
        cells = _guard_inventory_table_cells(line)
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


def _premise_guard_row(content: str, source_name: str) -> list[str]:
    rows = _guard_inventory_rows(content)
    matching = [row for row in rows if row[0] == PREMISE_GUARD_NAME]
    assert matching, (
        f"{source_name} guard inventory has no row whose Guard cell is exactly "
        f"{PREMISE_GUARD_NAME!r}; a fresh ralph --init project would ship without "
        f"the premise-check guard"
    )
    row = matching[0]
    assert len(row) == len(GUARD_INVENTORY_COLUMNS), (
        f"{source_name} guard-inventory row for {PREMISE_GUARD_NAME!r} carries "
        f"{len(row)} cells; the contract is {len(GUARD_INVENTORY_COLUMNS)} cells: "
        f"{list(GUARD_INVENTORY_COLUMNS)}"
    )
    empty_columns = [
        column for column, cell in zip(GUARD_INVENTORY_COLUMNS, row, strict=True) if not cell
    ]
    assert not empty_columns, (
        f"{source_name} guard-inventory row for {PREMISE_GUARD_NAME!r} has empty "
        f"cells: {empty_columns}"
    )
    return row


def _advisory_reason_and_trigger(enforcement: str, source_name: str) -> tuple[str, str]:
    lowered = enforcement.lower()
    assert lowered.startswith("advisory:"), (
        f"{source_name} guard-inventory row for {PREMISE_GUARD_NAME!r} must keep an "
        f"advisory enforcement; observed: {enforcement!r}"
    )
    marker = "; review:"
    index = lowered.find(marker)
    assert index != -1, (
        f"{source_name} guard-inventory row for {PREMISE_GUARD_NAME!r} must name its "
        f"review trigger; observed: {enforcement!r}"
    )
    reason = enforcement[len("advisory:") : index].strip()
    trigger = enforcement[index + len(marker) :].strip()
    return reason, trigger


def _parity_key(text: str) -> str:
    return " ".join("".join(char.lower() if char.isalnum() else " " for char in text).split())


def _assert_premise_guard_contract(content: str, source_name: str) -> str:
    row = _premise_guard_row(content, source_name)
    enforcement = row[GUARD_INVENTORY_COLUMNS.index("Enforcement")]
    protected_outcome = row[GUARD_INVENTORY_COLUMNS.index("Protected outcome")]
    claim_scope = row[GUARD_INVENTORY_COLUMNS.index("Claim scope")]
    reason, trigger = _advisory_reason_and_trigger(enforcement, source_name)
    reason_lower = reason.lower()
    for substring in PREMISE_ADVISORY_REASON_SUBSTRINGS:
        assert substring in reason_lower, (
            f"{source_name} guard-inventory row for {PREMISE_GUARD_NAME!r} does not "
            f"state the advisory reason {substring!r}; observed reason: {reason!r}"
        )
    trigger_lower = trigger.lower()
    assert any(substring in trigger_lower for substring in PREMISE_REVIEW_TRIGGER_SUBSTRINGS), (
        f"{source_name} guard-inventory row for {PREMISE_GUARD_NAME!r} names no review "
        f"trigger about the retry contract or the failure classifier; observed "
        f"trigger: {trigger!r}"
    )
    assert "premise" in protected_outcome.lower(), (
        f"{source_name} guard-inventory Protected outcome for {PREMISE_GUARD_NAME!r} "
        f"does not name the premise; observed: {protected_outcome!r}"
    )
    assert "premise" in claim_scope.lower(), (
        f"{source_name} guard-inventory Claim scope for {PREMISE_GUARD_NAME!r} does not "
        f"name the premise; observed: {claim_scope!r}"
    )
    return enforcement


def _assert_premise_guard_template_row(content: str, source_name: str) -> str:
    return _assert_premise_guard_contract(content, source_name)


def test_kernel_root_and_starter_guard_inventory_carry_the_premise_check_row() -> None:
    root_enforcement = _assert_premise_guard_contract(_root_kernel(), "root kernel")
    starter_enforcement = _assert_premise_guard_contract(_kernel(), "starter kernel")
    root_reason, root_trigger = _advisory_reason_and_trigger(root_enforcement, "root kernel")
    starter_reason, starter_trigger = _advisory_reason_and_trigger(
        starter_enforcement, "starter kernel"
    )
    assert _parity_key(root_reason) == _parity_key(starter_reason), (
        f"root kernel premise-check advisory reason {root_reason!r} differs from the "
        f"starter kernel advisory reason {starter_reason!r}"
    )
    assert _parity_key(root_trigger) == _parity_key(starter_trigger), (
        f"root kernel premise-check review trigger {root_trigger!r} differs from the "
        f"starter kernel review trigger {starter_trigger!r}"
    )


def _replace_guard_inventory_section(content: str, replacement: str) -> str:
    marker = f"\n{markers.GUARD_INVENTORY_HEADING}\n"
    start = content.index(marker) + len(marker)
    rest = content[start:]
    next_heading = rest.find("\n## ")
    end = start + (next_heading if next_heading != -1 else len(rest))
    return content[:start] + replacement + content[end:]


def _mutated_guard_inventory_texts(content: str) -> tuple[str, str]:
    section = _section(content, markers.GUARD_INVENTORY_HEADING)
    enforcement_index = GUARD_INVENTORY_COLUMNS.index("Enforcement")
    without_row: list[str] = []
    without_reason: list[str] = []
    premise_rows = 0
    for line in section.splitlines(keepends=True):
        cells = _guard_inventory_table_cells(line)
        is_premise_row = bool(cells) and cells[0] == PREMISE_GUARD_NAME
        if not is_premise_row:
            without_row.append(line)
            without_reason.append(line)
            continue
        premise_rows += 1
        without_row.append("")
        _reason, trigger = _advisory_reason_and_trigger(cells[enforcement_index], "root kernel")
        mutated_cells = list(cells)
        mutated_cells[enforcement_index] = f"advisory: ; review: {trigger}"
        ending = "\n" if line.endswith("\n") else ""
        without_reason.append("| " + " | ".join(mutated_cells) + " |" + ending)
    assert premise_rows == 1, (
        f"expected exactly one {PREMISE_GUARD_NAME!r} row in the guard inventory; "
        f"found {premise_rows}"
    )
    return (
        _replace_guard_inventory_section(content, "".join(without_row)),
        _replace_guard_inventory_section(content, "".join(without_reason)),
    )


def test_premise_guard_inventory_check_is_fault_sensitive_to_a_dropped_row_and_reason() -> None:
    without_row, without_reason = _mutated_guard_inventory_texts(_root_kernel())
    with pytest.raises(AssertionError, match="no row whose Guard cell is exactly"):
        _assert_premise_guard_contract(without_row, "root kernel without the premise row")
    with pytest.raises(AssertionError, match="does not state the advisory reason"):
        _assert_premise_guard_contract(without_reason, "root kernel without the reason")

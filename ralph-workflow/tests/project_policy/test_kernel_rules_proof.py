"""Tests for the v5 kernel rules and A2 protection-to-starter map.

The plan's A1 and A2 requirements anchor five kernel rules and six
A2 named protections in specific starter homes:

1. Truthful evidence
2. Fail-closed verification
3. Bounded execution
4. No weakening of checks
5. Explicit unverified-claim reporting

A2 protections:
* (1) security                -> security-policy.md
* (2) authorization / scope   -> security-policy.md
* (3) destructive operations  -> security-policy.md
* (4) process / resource      -> architecture-policy.md
* (5) deterministic seams     -> testing-policy.md
* (6) schema / data integrity -> data-storage-policy.md
"""

from __future__ import annotations

import re

import pytest

from ralph.project_policy import markers, starters

# The five kernel rule phrases (lowercased). Each must appear in
# the kernel starter, ideally as a heading or as the rule's
# canonical phrase.
KERNEL_RULES: tuple[str, ...] = (
    "truthful evidence",
    "fail-closed verification",
    "bounded execution",
    "no weakening of checks",
    "explicit unverified-claim reporting",
)

# Substantive mandatory obligation phrases required for each kernel rule.
# A rule is not satisfied merely by its title; its statement must carry
# the substantive requirement phrasing so that substituting retirement or
# placeholder prose causes a test failure (A1).
KERNEL_RULE_SUBSTANTIVE_OBLIGATIONS: dict[str, tuple[str, ...]] = {
    "truthful evidence": ("must be backed by a record",),
    "fail-closed verification": (
        "run does not proceed",
        "hard failure",
    ),
    "bounded execution": ("hard cap",),
    "no weakening of checks": (
        "never the gate",
        "violation",
    ),
    "explicit unverified-claim reporting": ("must mark it explicitly as unverified",),
}

# Negation, retirement, and deprecation phrases that MUST NOT appear
# in any kernel rule statement.
KERNEL_RULE_RETIREMENT_PHRASES: tuple[str, ...] = (
    "retired",
    "deprecated",
    "no longer required",
    "optional",
)


def _starter(name: str) -> str:
    return starters.read_starter(name)


def _kernel() -> str:
    return starters.read_starter("execution-kernel.md")


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
    """Extract individual rule statements mapped by their normalized rule name."""
    pattern = re.compile(r"(\d+\.\s+\*\*(.*?)\*\*[\s\S]*?)(?=\n\d+\.\s+\*\*|\Z)")
    rules: dict[str, str] = {}
    for match in pattern.finditer(section):
        block: str = match.group(1)
        raw_title: str = match.group(2)
        title: str = raw_title.rstrip(".").strip().lower()
        rules[title] = " ".join(block.split())
    return rules


def _verify_rule_statement(
    rule_name: str,
    statement: str,
    obligations: tuple[str, ...],
    retirement_phrases: tuple[str, ...] = KERNEL_RULE_RETIREMENT_PHRASES,
) -> None:
    """Verify that a single rule statement carries substantive obligations and no retirement phrases."""
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
    """The execution-kernel starter carries the five kernel rule
    phrases within its '## Kernel rules' section. Extracting that
    section must yield all five rules, assert substantive mandatory
    obligations for EACH rule, and verify that negation/retirement
    phrases do not appear in the rule statements (A1 fault-sensitive
    regression proof).
    """
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
    """Fault sensitivity: omitting any of the 5 rules fails the proof."""
    content = _kernel()
    section = _extract_kernel_rules_section(content)
    rules = _extract_rule_statements(section)
    for rule in KERNEL_RULES:
        mutated_rules = {k: v for k, v in rules.items() if k != rule}
        with pytest.raises(AssertionError, match="missing the .* rule"):
            for r in KERNEL_RULES:
                assert r in mutated_rules, f"## Kernel rules section missing the {r!r} rule"


def test_kernel_rules_proof_is_fault_sensitive_to_stripped_obligation() -> None:
    """Fault sensitivity: replacing substantive obligations with generic prose fails the proof."""
    for rule, obligations in KERNEL_RULE_SUBSTANTIVE_OBLIGATIONS.items():
        hollow_statement = f"1. **{rule.title()}.** Generic filler without substantive obligation."
        with pytest.raises(AssertionError, match="missing substantive mandatory obligation"):
            _verify_rule_statement(rule, hollow_statement, obligations)


@pytest.mark.parametrize("retirement_phrase", KERNEL_RULE_RETIREMENT_PHRASES)
def test_kernel_rules_proof_is_fault_sensitive_to_retirement_phrases(
    retirement_phrase: str,
) -> None:
    """Fault sensitivity: inserting retirement/negation phrases fails the proof."""
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
    """The kernel explicitly states it has precedence over domain
    policies. Without precedence the ``B10`` reduction (one
    versioned execution kernel) has no authority and the
    domain starters can re-derive the rules.
    """
    content = _kernel()
    normalized = " ".join(content.split()).lower()
    assert "precedence" in normalized
    assert "kernel" in normalized


def test_kernel_starter_declares_selective_applicability() -> None:
    """The kernel says agents load the kernel plus policies
    triggered by the changed surfaces, not every policy. This is
    the B2 anchor.
    """
    content = _kernel()
    normalized = " ".join(content.split()).lower()
    assert "selective" in normalized or "trigger" in normalized


def test_kernel_starter_declares_claim_scope_rule() -> None:
    """The kernel says a broad-guarantee phrase in a control's
    ``protected_outcome`` that does NOT also appear in
    ``detects`` is rejected. This is the B7 anchor.
    """
    content = _kernel()
    normalized = " ".join(content.split()).lower()
    # The substring predicate is enforced in
    # ``markers.CLAIM_SCOPE_PHRASES``; the kernel starter
    # MUST name the predicate or its consequence.
    has_claim_scope = "claim-scope" in normalized or "claim scope" in normalized
    has_broad_phrase = any(phrase.lower() in normalized for phrase in markers.CLAIM_SCOPE_PHRASES)
    assert has_claim_scope or has_broad_phrase, (
        "kernel starter does not name the claim-scope rule; the v5 B7 anchor is missing"
    )


def test_a2_security_protection_in_security_starter() -> None:
    """A2 protection (1): the security starter names the security
    protection in its body AND owns a guard-inventory row whose
    ``Protected outcome`` names security.
    """
    content = _starter("security-policy.md")
    normalized = " ".join(content.split()).lower()
    # The starter names the protection directly.
    assert "security" in normalized
    # The guard inventory carries a row that names security in
    # the ``Protected outcome`` cell.
    rows = _parse_inventory("security-policy.md")
    security_rows = [row for row in rows if "security" in row["protected_outcome"].lower()]
    assert security_rows, (
        "security starter guard-inventory missing a row whose "
        "Protected outcome names security; A2 protection (1) "
        "has no guard"
    )


def test_a2_authorization_protection_in_security_starter() -> None:
    """A2 protection (2): authorization / tenant / scope rules
    are owned by the security starter.
    """
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
    """A2 protection (3): destructive-operation safety rules are
    owned by the security starter.
    """
    content = _starter("security-policy.md")
    normalized = " ".join(content.split()).lower()
    assert "destructive" in normalized
    rows = _parse_inventory("security-policy.md")
    destructive_rows = [row for row in rows if "destructive" in row["protected_outcome"].lower()]
    assert destructive_rows, (
        "security starter guard-inventory missing a destructive row; A2 protection (3) has no guard"
    )


def test_a2_process_resource_ownership_in_architecture_starter() -> None:
    """A2 protection (4): process / resource ownership rules are
    owned by the architecture starter.
    """
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
    """A2 protection (5): deterministic clock / network / DNS /
    filesystem / process seams are owned by the testing starter.
    Every named seam MUST be present, not just one — DNS is
    required alongside the four I/O seams.
    """
    content = _starter("testing-policy.md")
    normalized = " ".join(content.split()).lower()
    # Every deterministic seam is required in the body, not any
    # one. A starter that names only clock is not A2-compliant.
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
    """A2 protection (6): schema / data-integrity rules are
    owned by the data-storage starter.
    """
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
    """B10: Domain starters must not repeat the generic 'weakened to obtain a
    passing result' phrase in their living document contracts (or anywhere in prose).
    Instead, they must point to execution-kernel.md (§Kernel rules) as the single
    authority for non-weakening and generic rule protection.
    """
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

        # 1. Must NOT contain the generic duplicated phrase
        assert generic_weakened_phrase not in lower_content, (
            f"{name} duplicates the generic kernel rule phrase "
            f"{generic_weakened_phrase!r} in its body; B10 requires pointing "
            f"to execution-kernel.md instead"
        )

        # 2. Must contain the kernel pointer in the Living document contract section
        assert "execution-kernel.md" in normalized_content, (
            f"{name} missing execution-kernel.md pointer"
        )
        assert pointer_sentence in normalized_content, (
            f"{name} missing canonical living document kernel pointer sentence: "
            f"{pointer_sentence!r}"
        )

    # 3. execution-kernel.md remains the single authority that retains the rule
    kernel_content = _kernel()
    normalized_kernel = " ".join(kernel_content.split()).lower()
    assert generic_weakened_phrase in normalized_kernel, (
        "execution-kernel.md must retain the canonical non-weakening rule"
    )


# Phrase patterns that paraphrase a kernel rule. Each is a low-friction
# rewrite that survives a literal-keyword filter but still re-derives the
# rule in domain starter body prose. The test below forbids them in
# every starter except execution-kernel.md, and except inside the two
# sections that legitimately name the kernel (## AI execution
# instructions pointer, ## Living document contract footer).
KERNEL_RULE_PARAPHRASE_PATTERNS: tuple[str, ...] = (
    # Rule 1 (Truthful evidence) paraphrases
    "i think it works",
    "actual command and outcome",
    "report the actual command",
    "report the actual outcome",
    # Rule 2 (Fail-closed verification) paraphrase
    "report failures accurately",
    # Rule 4 (No weakening of checks) paraphrases
    "obtain a passing result",
    "obtaining a passing result",
    "weaken a check to obtain",
    "weakening a check to obtain",
    # Rule 5 (Explicit unverified-claim reporting) paraphrase
    "avoid unsupported claims",
    "must not make unsupported claims",
)


def _body_sans_kernel_pointer_sections(content: str) -> str:
    """Return the starter body with the two kernel-reference
    sections removed.

    The ``## AI execution instructions`` section is the canonical
    pointer location; the ``## Living document contract`` footer is
    the canonical single-source-of-truth attribution. Both
    legitimately name the kernel rules; the dedup test must not
    fire on text inside either.
    """
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
    """B10 body-level dedup: a domain starter MUST NOT paraphrase any
    of the five kernel rules in body prose. The ``## AI execution
    instructions`` pointer section and the ``## Living document
    contract`` footer are the only two locations where the kernel is
    legitimately named; both are excluded by ``_body_sans_kernel_pointer_sections``.

    The existing phrase-list test in ``test_v5_starter_proof.py`` only
    catches a narrow set of verbatim phrases. This test catches the
    semantic restatements (e.g. ``I think it works``, ``obtain a
    passing result``, ``avoid unsupported claims``) that a future
    editor would naturally reach for when collapsing the body. A
    regression is a future starter edit that re-introduces one of
    the paraphrased rules in any non-pointer, non-footer section.
    """
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

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

from ralph.project_policy import markers, starters

# The five kernel rule phrases (lowercased). Each must appear in
# the kernel starter, ideally as a heading or as the rule's
# canonical phrase.
KERNEL_RULES: tuple[str, ...] = (
    "truthful evidence",
    "fail-closed verification",
    "bounded execution",
    "weakening of checks",
    "unverified",
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


def test_kernel_starter_carries_all_five_rules() -> None:
    """The execution-kernel starter carries the five kernel rule
    phrases. A regression that drops one is a v5 contract
    violation: the kernel is the single source of the generic
    rules and any other starter that needs them MUST load from
    here.
    """
    content = _kernel()
    normalized = " ".join(content.split()).lower()
    for rule in KERNEL_RULES:
        assert rule.lower() in normalized, (
            f"kernel starter missing the {rule!r} rule; the "
            f"five-rule contract is the v5 deliverable"
        )


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

from __future__ import annotations

from ralph.project_policy import markers, starters


def test_testing_rule_13_already_protected_qualifier() -> None:
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    assert "Negative coverage is mandatory" in normalized
    assert "already protected" in normalized
    assert "does NOT duplicate coverage" in normalized


def test_testing_browser_budget_distinct_risk_for_every_journey() -> None:
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    assert "BELOW the measured budget" in normalized
    assert "AT or OVER the measured budget" in normalized
    assert "WITHOUT any mandatory time offset" in normalized
    assert "EVERY retained browser journey" in normalized
    assert "never raised" in normalized


def test_testing_high_churn_evidence_section_present() -> None:
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    assert "## High-churn evidence" in normalized
    assert "per-run" in normalized
    assert "mutation" in normalized or "coverage" in normalized
    assert "ignored paths" in normalized or "CI artifacts" in normalized


def test_verification_high_churn_evidence_section_present() -> None:
    content = starters.read_starter("verification-policy.md")
    normalized = " ".join(content.split())
    assert "## High-churn evidence" in normalized
    assert "per-run" in normalized
    assert "mutation" in normalized or "coverage" in normalized
    assert "ignored paths" in normalized or "CI artifacts" in normalized


def test_testing_kernel_pointer_is_present() -> None:
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    assert "execution-kernel.md" in normalized
    assert "do not re-derive them here" in normalized


def test_architecture_kernel_pointer_is_present() -> None:
    content = starters.read_starter("architecture-policy.md")
    normalized = " ".join(content.split())
    assert "execution-kernel.md" in normalized
    assert "do not re-derive them here" in normalized
    assert "testing-policy.md" in normalized


def test_clean_code_kernel_pointer_is_present() -> None:
    content = starters.read_starter("clean-code-policy.md")
    normalized = " ".join(content.split())
    assert "execution-kernel.md" in normalized
    assert "do not re-derive them here" in normalized


def test_typechecking_linting_security_kernel_pointer_is_present() -> None:
    for name in (
        "typechecking-policy.md",
        "linting-policy.md",
        "security-policy.md",
    ):
        content = starters.read_starter(name)
        normalized = " ".join(content.split())
        assert "execution-kernel.md" in normalized, f"{name} missing kernel pointer"
        assert "do not re-derive them here" in normalized, f"{name} does not disclaim re-derivation"


def test_all_domain_starters_carry_kernel_pointer() -> None:
    all_domain_starters = [
        name
        for name in starters.STARTER_NAMES
        if name != "execution-kernel.md" and name.endswith(".md")
    ]
    for name in all_domain_starters:
        content = starters.read_starter(name)
        normalized = " ".join(content.split())
        assert "execution-kernel.md" in normalized, f"{name} missing kernel pointer"
        assert "do not re-derive them here" in normalized, (
            f"{name} does not disclaim re-derivation of kernel rules"
        )


def test_claim_scope_substring_predicate_pinned() -> None:
    assert markers.CLAIM_SCOPE_PHRASES == (
        "enforced",
        "guaranteed",
        "ensures",
        "always",
        "prevents all",
        "all ",
    )


def test_guard_inventory_pinned_grammar() -> None:
    assert markers.GUARD_INVENTORY_HEADING == "## Guard inventory"
    assert markers.GUARD_INVENTORY_HEADER == (
        "Guard",
        "Owner",
        "Protected outcome",
        "Detected condition",
        "Claim scope",
        "Enforcement",
        "Retirement trigger",
    )


def test_kernel_marks_known_threat_surfaces() -> None:
    content = starters.read_starter("security-policy.md")
    normalized = " ".join(content.split())
    for surface in (
        "Authorization",
        "tenant",
        "destructive",
    ):
        assert surface in normalized or surface.lower() in normalized, (
            f"security starter missing threat surface: {surface!r}"
        )
    assert markers.GUARD_INVENTORY_HEADING in content


def test_domain_starters_do_not_duplicate_kernel_truthful_evidence_rule() -> None:
    domain_starters = [
        name
        for name in starters.STARTER_NAMES
        if name != "execution-kernel.md" and name.endswith(".md")
    ]
    pinned_prohibited_phrases = (
        "report the actual outcome",
        "never report a command",
        "never report a command that was not run",
    )
    for name in domain_starters:
        content = starters.read_starter(name)
        lower_content = " ".join(content.split()).lower()
        for phrase in pinned_prohibited_phrases:
            assert phrase not in lower_content, f"{name} duplicates kernel-owned phrase {phrase!r}"
    kernel = starters.read_starter("execution-kernel.md")
    assert "never report a command that was not run" in kernel.lower()


def test_b10_kernel_owned_concepts_not_re_derived_inline() -> None:
    domain_starters = [
        name
        for name in starters.STARTER_NAMES
        if name != "execution-kernel.md" and name.endswith(".md")
    ]
    pinned_prohibited_phrases = (
        "report the actual outcome",
        "never report a command",
        "never report a command that was not run",
        "a check that cannot run blocks the run",
        "every long-running action carries a runner-enforced cap",
        "the only lawful way past a red gate",
        "weakened to obtain a passing result",
        "mark it explicitly as unverified",
    )
    for name in domain_starters:
        content = starters.read_starter(name)
        lower_content = " ".join(content.split()).lower()
        for phrase in pinned_prohibited_phrases:
            assert phrase not in lower_content, (
                f"{name} duplicates kernel-owned phrase {phrase!r}; "
                f"kernel rule must not be re-derived in domain starter prose"
            )


def test_architecture_starter_uses_generic_resource_lifecycle_guard() -> None:
    arch = starters.read_starter("architecture-policy.md")
    assert "Process and resource ownership" in arch
    assert "resource-lifecycle checks return zero findings" in arch
    assert "audit_resource_lifecycle" not in arch


def test_testing_cheapest_layer_rule_14_present() -> None:
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    assert (
        "Every bug fix to behaviour in the automated lane MUST add a regression test that fails on the bug and passes on the fix"
        in normalized
    )
    assert "Shape the suite so the cheapest" in normalized


def test_testing_guard_rows_real_boundary_set_substantive_prose() -> None:
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    assert "Real-boundary set" in normalized
    assert "at the real boundary" in normalized


def test_testing_guard_rows_browser_budget_substantive_prose() -> None:
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    assert "Browser budget, not cardinality" in normalized
    assert "distinct-risk inventory" in normalized


def test_testing_guard_row_narrowest_observable_boundary_substantive_prose() -> None:
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    assert "Narrowest observable boundary" in normalized
    assert "private method" in normalized
    assert "wrapper solely" in normalized


def test_typechecking_guard_row_unknown_at_untrusted_boundaries_substantive_prose() -> None:
    content = starters.read_starter("typechecking-policy.md")
    normalized = " ".join(content.split())
    assert "Unknown at untrusted boundaries" in normalized
    assert "immediate narrowing" in normalized


def test_clean_code_syntax_proxy_ban_present() -> None:
    content = starters.read_starter("clean-code-policy.md")
    normalized = " ".join(content.split())
    assert "Syntax-proxy ban" in normalized
    assert "private method is renamed to public solely" in normalized

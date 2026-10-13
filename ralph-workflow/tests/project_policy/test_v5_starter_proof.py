"""Regression tests for the v5 starter prose contracts.

The v5 plan (B3, B5, B8, B10) and the analysis feedback added
specific prose obligations to the shipped starter templates. These
tests pin the contract at the cheapest deterministic layer so a
future editor cannot silently re-collapse a deliberate carve-out
(e.g. the B5 distinct-risk rule, the B3 already-protected
qualifier, the B8 high-churn evidence rule, the B10 kernel
pointer) back into a generic one-liner.
"""

from __future__ import annotations

from ralph.project_policy import markers, starters


def test_testing_rule_13_already_protected_qualifier() -> None:
    """B3: rule 13's "mandatory negative" must include the
    ``already protected`` qualifier so a duplicate test cannot be
    justified by claiming the behavior is unprotected when the
    actual rule's protection lives at a cheaper sufficient layer.
    """
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    # Locate rule 13's body. The numbering is fragile, so we check
    # for the surrounding context that rule 13 is the negative-
    # coverage rule.
    assert "Negative coverage is mandatory" in normalized
    assert "already protected" in normalized
    # The "no duplication" wording must also be present so a
    # reviewer can show the qualifier is real, not rhetorical.
    assert "does NOT duplicate coverage" in normalized


def test_testing_browser_budget_distinct_risk_for_every_journey() -> None:
    """B5: the browser-budget rule must require a distinct
    browser-only risk for EVERY retained journey, not only at/over
    the budget. A retained journey without a distinct browser-only
    risk is rejectable even below the budget.
    """
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    # Two states, both present and unambiguous.
    assert "BELOW the measured budget" in normalized
    assert "AT or OVER the measured budget" in normalized
    # Below-budget admission does NOT require a mandatory offset
    # (the experimental relaxation), but does require a
    # distinct-browser-only-risk justification.
    assert "WITHOUT any mandatory time offset" in normalized
    # Every retained journey, regardless of budget, must cover a
    # distinct browser-only risk; this is the carve-out that the
    # analysis feedback requires.
    assert "EVERY retained browser journey" in normalized
    # The aggregate ceiling is never raised.
    assert "never raised" in normalized


def test_testing_high_churn_evidence_section_present() -> None:
    """B8: the testing starter must have a high-churn evidence
    section so volatile reports and large generated inventories
    never reach human review as a thousand-line commit.
    """
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    assert "## High-churn evidence" in normalized
    # The section must name at least the canonical examples the
    # plan calls out (per-run logs, mutation dumps, generated
    # coverage).
    assert "per-run" in normalized
    assert "mutation" in normalized or "coverage" in normalized
    # The rule must mention ignored paths or CI artifacts so the
    # route for volatile evidence is explicit.
    assert "ignored paths" in normalized or "CI artifacts" in normalized


def test_testing_kernel_pointer_is_present() -> None:
    """B10: the testing starter's AI execution instructions must
    point to ``execution-kernel.md`` for the five generic rules
    rather than re-derive them inline.
    """
    content = starters.read_starter("testing-policy.md")
    normalized = " ".join(content.split())
    assert "execution-kernel.md" in normalized
    # The pointer must explicitly name the kernel as the owner of
    # the five rules and the precedence / selective-applicability
    # rule, so a future edit that re-derives them inline is a
    # regression.
    assert "do not re-derive them here" in normalized


def test_architecture_kernel_pointer_is_present() -> None:
    """B10: the architecture starter also points to the kernel
    and explicitly notes that exposing a private method solely to
    satisfy a test is a testing-policy violation, not a fitting
    architectural response.
    """
    content = starters.read_starter("architecture-policy.md")
    normalized = " ".join(content.split())
    assert "execution-kernel.md" in normalized
    assert "do not re-derive them here" in normalized
    # The cross-reference to the testing starter is the
    # contradiction-pair alignment (B11) anchor.
    assert "testing-policy.md" in normalized


def test_clean_code_kernel_pointer_is_present() -> None:
    """B10: the clean-code starter points to the kernel and
    explicitly disclaims the "make it public to satisfy a
    ratchet" transformation as a violation rather than a fix.
    """
    content = starters.read_starter("clean-code-policy.md")
    normalized = " ".join(content.split())
    assert "execution-kernel.md" in normalized
    assert "do not re-derive them here" in normalized


def test_typechecking_linting_security_kernel_pointer_is_present() -> None:
    """B10: the typechecking, linting, and security starters all
    point to the kernel and reject the syntactic-proxy
    transformation.
    """
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
    """B10: every domain starter (core and conditional, all starters
    except execution-kernel.md itself) MUST point to execution-kernel.md
    so the kernel rules are not re-stated per domain starter.
    """
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
    """B7: the claim-scope phrase list is exactly the pinned set.

    A future edit that drops "all " (note the trailing space) is
    silently weakened: "enforces all checks" would then no longer
    trigger the predicate, and the very common "all checks" row
    in a control's ``protected_outcome`` would slip through.
    """
    assert markers.CLAIM_SCOPE_PHRASES == (
        "enforced",
        "guaranteed",
        "ensures",
        "always",
        "prevents all",
        "all ",
    )


def test_guard_inventory_pinned_grammar() -> None:
    """B9: the guard-inventory grammar is pinned in markers so
    the parser and the starter content share one source of truth.
    A future edit that widens the table grammar is a regression.
    """
    assert markers.GUARD_INVENTORY_HEADING == "## Guard inventory"
    assert markers.GUARD_INVENTORY_HEADER == (
        "Guard",
        "Owner",
        "Protected outcome",
        "Detected condition",
        "Retirement trigger",
    )


def test_kernel_marks_known_threat_surfaces() -> None:
    """A2: the security starter's threat surfaces (security,
    authorization/tenant-scope, destructive operations) MUST
    appear in the body AND each surface must own a guard-inventory
    row whose ``Protected outcome`` names the surface.

    The guard-inventory row is the durable contract: a security
    section that just lists surfaces in prose can be rephrased
    away, but a row keyed on the surface name anchors it.
    """
    content = starters.read_starter("security-policy.md")
    normalized = " ".join(content.split())
    # The three surfaces are named in the prose.
    for surface in (
        "Authorization",
        "tenant",
        "destructive",
    ):
        assert surface in normalized or surface.lower() in normalized, (
            f"security starter missing threat surface: {surface!r}"
        )
    # The guard inventory has a row for each surface. We check
    # the heading is present (the rows are validated by the
    # parser in test_v5_kernel.py).
    assert markers.GUARD_INVENTORY_HEADING in content

from __future__ import annotations

import pytest

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import (
    PortfolioError,
    agents_md,
    markers,
    parse_portfolio_toml,
    validators,
)
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus


def _make_enforcement_workspace(enforcement_val: str) -> MemoryWorkspace:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    path = f"{markers.CANONICAL_DIR}typechecking-policy.md"
    body = ws.read(path).replace("deterministic: make test", enforcement_val)
    ws.write(path, body)
    return ws


def test_v6_constants_pinned() -> None:
    assert markers.SCHEMA_VERSION == "v6"
    assert markers.POLICY_SCHEMA_MARKER == "<!-- ralph-policy-schema: v6 -->"
    assert markers.POLICY_CONTRACT_VERSION == "2026-11-09.1"
    assert markers.PORTFOLIO_SCHEMA_VERSION == "v3"
    assert markers.ENFORCEMENT_MISSING_FINDING_ID == "RWP-INVENTORY:ENFORCEMENT-MISSING"
    assert markers.ENFORCEMENT_UNKNOWN_ID_FINDING_ID == "RWP-INVENTORY:ENFORCEMENT-UNKNOWN-ID"
    assert (
        markers.ENFORCEMENT_ADVISORY_NO_TRIGGER_FINDING_ID
        == "RWP-INVENTORY:ENFORCEMENT-ADVISORY-NO-TRIGGER"
    )
    assert len(markers.GUARD_INVENTORY_HEADER) == 7
    assert markers.GUARD_INVENTORY_HEADER == (
        "Guard",
        "Owner",
        "Protected outcome",
        "Detected condition",
        "Claim scope",
        "Enforcement",
        "Retirement trigger",
    )


def test_header_column_matrix_v4_v5_v6() -> None:
    v4_spec = markers.spec_for_version("v4")
    assert v4_spec is not None
    assert v4_spec.guard_inventory_header is None
    assert v4_spec.guard_inventory_columns == 0

    v5_spec = markers.spec_for_version("v5")
    assert v5_spec is not None
    assert v5_spec.guard_inventory_header is not None
    assert v5_spec.guard_inventory_columns == 6

    v6_spec = markers.spec_for_version("v6")
    assert v6_spec is not None
    assert v6_spec.guard_inventory_header is not None
    assert v6_spec.guard_inventory_columns == 7


def test_enforcement_missing_cell_fires_finding() -> None:
    ws = _make_enforcement_workspace("")
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(f.requirement_id == markers.ENFORCEMENT_MISSING_FINDING_ID for f in findings)


def test_enforcement_invalid_syntax_fires_missing_finding() -> None:
    ws = _make_enforcement_workspace("unsupported enforcement string")
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(f.requirement_id == markers.ENFORCEMENT_MISSING_FINDING_ID for f in findings)


def test_enforcement_deterministic_empty_id_fires_missing_finding() -> None:
    ws = _make_enforcement_workspace("deterministic: ")
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(f.requirement_id == markers.ENFORCEMENT_MISSING_FINDING_ID for f in findings)


def test_enforcement_deterministic_unknown_id_fires_finding() -> None:
    ws = _make_enforcement_workspace("deterministic: unknown-command-id")
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(f.requirement_id == markers.ENFORCEMENT_UNKNOWN_ID_FINDING_ID for f in findings)


def test_enforcement_deterministic_valid_id_accepted() -> None:
    ws = _make_enforcement_workspace("deterministic: make test")
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert not any("ENFORCEMENT" in f.requirement_id for f in findings)


def test_enforcement_advisory_no_trigger_fires_finding() -> None:
    ws1 = _make_enforcement_workspace("advisory: subjective review only")
    findings1 = validators.validate_readiness(ws1, ProjectStack(primary_language="Python"))
    assert any(
        f.requirement_id == markers.ENFORCEMENT_ADVISORY_NO_TRIGGER_FINDING_ID for f in findings1
    )

    ws2 = _make_enforcement_workspace("advisory: subjective review; review: ")
    findings2 = validators.validate_readiness(ws2, ProjectStack(primary_language="Python"))
    assert any(
        f.requirement_id == markers.ENFORCEMENT_ADVISORY_NO_TRIGGER_FINDING_ID for f in findings2
    )


def test_enforcement_advisory_blank_reason_fires_finding() -> None:
    ws = _make_enforcement_workspace("advisory: ; review: every release")
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(
        f.requirement_id == markers.ENFORCEMENT_MISSING_FINDING_ID
        and "empty reason" in f.missing_evidence
        for f in findings
    )


def test_enforcement_advisory_valid_accepted() -> None:
    ws = _make_enforcement_workspace("advisory: subjective check; review: on every API release")
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert not any("ENFORCEMENT" in f.requirement_id for f in findings)


def test_portfolio_v3_enforcement_validation() -> None:
    base_toml = """
schema_version = "v3"
default_budget_seconds = 3.0
kernel_controls = ["c1"]

[context]
impact = "low"
likelihood = "low"
uncertainty = "low"
recoverability = "high"
architectures = ["library"]
obligations = []

[[lanes]]
id = "default"
owner = "quality"
trigger = "every change"

[[controls]]
id = "c1"
protected_outcome = "c1 outcome"
fault_sensitivity = "c1 distinct failure"
layer = "unit"
owner = "quality"
trigger = "every change"
lane = "default"
marginal_cost_seconds = 1.0
cost_basis = "estimate"
evidence = "artifact:c1"
detects = "c1 detected condition"
retirement_trigger = "when c1 is retired"
lifecycle = "review when protected outcome changes"
disposition = "KEEP"
"""
    with pytest.raises(PortfolioError) as exc_missing:
        parse_portfolio_toml(base_toml)
    assert "enforcement" in str(exc_missing.value).lower()

    with pytest.raises(PortfolioError) as exc_invalid:
        parse_portfolio_toml(base_toml + 'enforcement = "invalid"\n')
    assert "enforcement" in str(exc_invalid.value).lower()

    valid_det = parse_portfolio_toml(base_toml + 'enforcement = "deterministic: make test"\n')
    assert valid_det.controls[0].enforcement == "deterministic: make test"

    valid_adv = parse_portfolio_toml(
        base_toml + 'enforcement = "advisory: reason; review: on trigger"\n'
    )
    assert valid_adv.controls[0].enforcement == "advisory: reason; review: on trigger"

    with pytest.raises(PortfolioError) as exc_blank_reason:
        parse_portfolio_toml(base_toml + 'enforcement = "advisory: ; review: on trigger"\n')
    assert "reason" in str(exc_blank_reason.value).lower()

    with pytest.raises(PortfolioError) as exc_blank_trigger:
        parse_portfolio_toml(base_toml + 'enforcement = "advisory: reason; review: "\n')
    assert "review trigger" in str(exc_blank_trigger.value).lower()

    with pytest.raises(PortfolioError) as exc_no_trigger:
        parse_portfolio_toml(base_toml + 'enforcement = "advisory: reason"\n')
    assert "review" in str(exc_no_trigger.value).lower()

    with pytest.raises(PortfolioError) as exc_placeholder:
        parse_portfolio_toml(
            base_toml + 'enforcement = "deterministic: PROJECT-FACT-UNRESOLVED"\n',
            known_enforcers=frozenset({"make test"}),
        )
    assert "placeholder" in str(exc_placeholder.value).lower()

    with pytest.raises(PortfolioError) as exc_unknown:
        parse_portfolio_toml(
            base_toml + 'enforcement = "deterministic: unknown-id"\n',
            known_enforcers=frozenset({"make test"}),
        )
    assert "unknown" in str(exc_unknown.value).lower()


def test_unknown_enforcement_id_in_policy_file_fails_closed() -> None:
    from ralph.language_detector.models import ProjectStack
    from ralph.project_policy import validators
    from tests.project_policy.policy_corpus import seed_complete_corpus

    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    path = f"{markers.CANONICAL_DIR}security-policy.md"
    body = ws.read(path).replace("deterministic: make test", "deterministic: make undeclared-check")
    ws.write(path, body)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(f.requirement_id == markers.ENFORCEMENT_UNKNOWN_ID_FINDING_ID for f in findings)


def test_managed_block_idempotent_rerender() -> None:
    ws = MemoryWorkspace()
    agents_md.bootstrap(ws)
    content_first = ws.read(markers.AGENTS_MD)
    assert markers.AGENTS_BLOCK_BEGIN in content_first
    assert markers.AGENTS_BLOCK_END in content_first

    agents_md.bootstrap(ws)
    content_second = ws.read(markers.AGENTS_MD)
    assert content_first == content_second
    assert content_second.count(markers.AGENTS_BLOCK_BEGIN) == 1
    assert content_second.count(markers.AGENTS_BLOCK_END) == 1


def test_starter_placeholder_banner_blocks_readiness() -> None:
    ws = MemoryWorkspace()
    for name in markers.CORE_POLICY_FILES:
        path = f"{markers.CANONICAL_DIR}{name}"
        ws.write(
            path,
            f"<!-- ralph-policy-schema: v6 -->\n"
            f"<!-- ralph-policy-id: {name} -->\n"
            f"<!-- {markers.STARTER_TEMPLATE_TOKEN}: banner -->\n"
            f"# {name}\n",
        )
    findings = validators.validate_readiness(
        ws,
        ProjectStack(primary_language="Python", secondary_languages=[], frameworks=[]),
    )
    assert any(
        f.requirement_id.endswith(":starter-template-banner")
        or markers.STARTER_TEMPLATE_TOKEN in f.missing_evidence
        for f in findings
    )

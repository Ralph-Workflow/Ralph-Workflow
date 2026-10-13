"""Tests for v5 execution-kernel starter and its content contract.

The v5 plan adds an outcome-oriented execution kernel (one of the
twelve required core files) plus the deterministic guard-inventory
parser and the claim-scope substring predicate. These tests cover the
new shapes at the cheapest deterministic layer.
"""

from __future__ import annotations

import pytest

from ralph.project_policy import _guard_inventory, markers, portfolio, starters
from ralph.workspace.memory import MemoryWorkspace


def test_kernel_starter_is_registered_in_v5_manifest() -> None:
    """The execution-kernel starter is the v5 required core file."""
    assert "execution-kernel.md" in markers.CORE_POLICY_FILES
    assert "execution-kernel.md" in starters.STARTER_NAMES
    assert markers.SCHEMA_VERSION == "v5"


def test_kernel_starter_carries_v5_marker_and_id() -> None:
    """The kernel starter uses the v5 schema marker and the kernel policy-id."""
    content = starters.read_starter("execution-kernel.md")
    first_line = next(line for line in content.splitlines() if line.strip())
    assert first_line == markers.POLICY_SCHEMA_MARKER
    assert "<!-- ralph-policy-id: execution-kernel.md -->" in content


def test_kernel_starter_has_guard_inventory_section() -> None:
    """The kernel starter carries the pinned ``## Guard inventory`` table."""
    content = starters.read_starter("execution-kernel.md")
    rows = _guard_inventory._parse_guard_inventory(content)
    assert rows, "execution-kernel.md must carry at least one guard row"
    for row in rows:
        for column in (
            "guard",
            "owner",
            "protected_outcome",
            "detected_condition",
            "retirement_trigger",
        ):
            assert row[column], f"row missing {column!r}: {row}"


def test_kernel_starter_required_headings_are_known() -> None:
    """The execution-kernel starter's required headings are pinned in markers."""
    content = starters.read_starter("execution-kernel.md")
    required = markers.REQUIRED_HEADINGS["execution-kernel.md"]
    headings = {
        h.lower()
        for h in (
            line.lstrip("# ").strip() for line in content.splitlines() if line.startswith("## ")
        )
    }
    for heading in required:
        assert heading.lower() in headings, f"missing required heading {heading!r}"


def test_kernel_starter_does_not_rephrase_its_own_placeholder_banner() -> None:
    """The kernel starter ships with the same RALPH-STARTER-TEMPLATE banner as
    every other starter; the kernel re-statement is in the body, not the
    template banner."""
    content = starters.read_starter("execution-kernel.md")
    assert markers.STARTER_TEMPLATE_TOKEN in content


def test_kernel_required_headings_include_guard_inventory() -> None:
    """The kernel's required headings include the guard inventory section (B9)."""
    required = markers.REQUIRED_HEADINGS["execution-kernel.md"]
    assert markers.GUARD_INVENTORY_HEADING.removeprefix("## ") in required


@pytest.mark.parametrize(
    "starter_name",
    [
        "testing-policy.md",
        "typechecking-policy.md",
        "linting-policy.md",
        "security-policy.md",
        "architecture-policy.md",
        "data-storage-policy.md",
    ],
)
def test_every_applicable_starter_carries_guard_inventory(starter_name: str) -> None:
    """Every applicable starter (core + conditional) carries a guard inventory."""
    content = starters.read_starter(starter_name)
    rows = _guard_inventory._parse_guard_inventory(content)
    assert rows, f"{starter_name} must carry at least one guard row"


def test_guard_inventory_parser_rejects_missing_section() -> None:
    """A starter without a ``## Guard inventory`` section fails the parser."""
    findings = _guard_inventory._check_guard_inventory(
        "## Purpose and scope\n\nReal content.\n",
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )
    assert any("missing" in finding.requirement_id for finding in findings), findings


def test_guard_inventory_parser_rejects_malformed_table() -> None:
    """A starter with a heading but malformed table fails the parser."""
    body = "## Guard inventory\n\n| wrong | header |\n|---|\n"
    findings = _guard_inventory._check_guard_inventory(
        body,
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )
    assert any("malformed" in finding.requirement_id for finding in findings), findings


def test_guard_inventory_parser_rejects_broad_claim() -> None:
    """A row with a broad-guarantee phrase that is not mirrored in detected
    condition is rejected (the deterministic claim-scope rule, B7)."""
    body = (
        "## Guard inventory\n"
        "\n"
        "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Retirement trigger |\n"
        "|---|---|---|---|---|---|\n"
        "| g | o | dependency direction enforced | missing import-lint | retired | the lint report per file |"
    )
    findings = _guard_inventory._check_guard_inventory(
        body,
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )
    assert any("claim-scope" in finding.requirement_id for finding in findings), findings


def test_guard_inventory_parser_accepts_mirrored_claim() -> None:
    """A row whose broad phrase also appears in detected condition is accepted."""
    body = (
        "## Guard inventory\n"
        "\n"
        "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Retirement trigger |\n"
        "|---|---|---|---|---|---|\n"
        "| g | o | dependency direction enforced | enforced by import-lint on src/ | retired | enforced per import-lint on src/ |"
    )
    findings = _guard_inventory._check_guard_inventory(
        body,
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )
    assert findings == [], findings


def test_guard_inventory_parser_rejects_empty_cells() -> None:
    """A row with an empty cell is rejected."""
    body = (
        "## Guard inventory\n"
        "\n"
        "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Retirement trigger |\n"
        "|---|---|---|---|---|---|\n"
        "| g |  | some outcome | some detected | some retirement | the report per file |"
    )
    findings = _guard_inventory._check_guard_inventory(
        body,
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )
    assert any("empty-owner" in finding.requirement_id for finding in findings), findings


def test_portfolio_v2_requires_detects_field() -> None:
    """Portfolio v2 requires ``detects`` on every control."""
    body = """
schema_version = "v2"
default_budget_seconds = 3.0
kernel_controls = ["kernel"]

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
id = "kernel"
protected_outcome = "kernel outcome"
fault_sensitivity = "kernel distinct failure"
layer = "unit"
owner = "quality"
trigger = "every change"
lane = "default"
marginal_cost_seconds = 1.0
cost_basis = "estimate"
evidence = "artifact:kernel"
retirement_trigger = "when the kernel control is retired"
lifecycle = "review when protected outcome changes"
disposition = "KEEP"
"""
    with pytest.raises(portfolio.PortfolioError) as excinfo:
        portfolio.parse_portfolio_toml(body)
    assert "detects" in str(excinfo.value).lower()


def test_portfolio_v2_requires_retirement_trigger_field() -> None:
    """Portfolio v2 requires ``retirement_trigger`` on every control."""
    body = """
schema_version = "v2"
default_budget_seconds = 3.0
kernel_controls = ["kernel"]

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
id = "kernel"
protected_outcome = "kernel outcome"
fault_sensitivity = "kernel distinct failure"
layer = "unit"
owner = "quality"
trigger = "every change"
lane = "default"
marginal_cost_seconds = 1.0
cost_basis = "estimate"
evidence = "artifact:kernel"
detects = "kernel detected condition"
lifecycle = "review when protected outcome changes"
disposition = "KEEP"
"""
    with pytest.raises(portfolio.PortfolioError) as excinfo:
        portfolio.parse_portfolio_toml(body)
    assert "retirement_trigger" in str(excinfo.value).lower()


def test_portfolio_v2_rejects_broad_phrase_in_protected_outcome() -> None:
    """A v2 control with a broad-guarantee phrase is rejected (B7)."""
    body = """
schema_version = "v2"
default_budget_seconds = 3.0
kernel_controls = ["kernel"]

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
id = "kernel"
protected_outcome = "dependency direction enforced"
fault_sensitivity = "kernel distinct failure"
layer = "unit"
owner = "quality"
trigger = "every change"
lane = "default"
marginal_cost_seconds = 1.0
cost_basis = "estimate"
evidence = "artifact:kernel"
detects = "missing import-lint finding"
retirement_trigger = "when the kernel control is retired"
lifecycle = "review when protected outcome changes"
disposition = "KEEP"
"""
    with pytest.raises(portfolio.PortfolioError) as excinfo:
        portfolio.parse_portfolio_toml(body)
    assert "enforced" in str(excinfo.value).lower()


def test_portfolio_v2_accepts_mirrored_claim() -> None:
    """A v2 control whose broad phrase also appears in detects is accepted."""
    body = """
schema_version = "v2"
default_budget_seconds = 3.0
kernel_controls = ["kernel"]

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
id = "kernel"
protected_outcome = "dependency direction enforced"
fault_sensitivity = "kernel distinct failure"
layer = "unit"
owner = "quality"
trigger = "every change"
lane = "default"
marginal_cost_seconds = 1.0
cost_basis = "estimate"
evidence = "artifact:kernel"
detects = "enforced by import-lint run on src/"
retirement_trigger = "when the kernel control is retired"
lifecycle = "review when protected outcome changes"
disposition = "KEEP"
"""
    portfolio.parse_portfolio_toml(body)


def test_validators_frozen_v4_pack_uses_v4_manifest() -> None:
    """A frozen v4 pack is validated against the v4 manifest, not v5.

    A v4-frozen pack missing ``execution-kernel.md`` is still READY
    because the v4 manifest does not require it; a v4-frozen pack
    missing ``security-policy.md`` stays NOT READY because the v4
    manifest still requires it.
    """
    from ralph.project_policy import validators

    workspace = MemoryWorkspace()
    # Bootstrap the v4 manifest (11 core files) without execution-kernel.
    for name in markers.core_files_for_version("v4"):
        path = f"{markers.CANONICAL_DIR}{name}"
        workspace.write(path, "<!-- ralph-policy-schema: freeze v4 -->\n")
    # Plus a minimal AGENTS.md + portfolio so the surface checks pass.
    workspace.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    workspace.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )
    workspace.write(
        markers.PORTFOLIO_PATH,
        'schema_version = "v1"\ndefault_budget_seconds = 1.0\nkernel_controls = []\n',
    )
    stack = type(
        "S", (), {"primary_language": "Python", "secondary_languages": (), "frameworks": ()}
    )()
    findings = validators.validate_readiness(workspace, stack)
    # The frozen-v4 manifest excludes execution-kernel.md, so the
    # missing-kernel finding is NOT emitted. The frozen v4 pack is
    # accepted as structurally valid; semantic checks (commands, facts)
    # are not asserted here because the synthesized body is intentionally
    # minimal in this test.
    requirement_ids = {finding.requirement_id for finding in findings}
    assert "execution-kernel.md" not in " ".join(requirement_ids), (
        f"frozen v4 manifest should not require execution-kernel.md; got: {requirement_ids}"
    )


def test_validators_frozen_v4_pack_missing_security_fails() -> None:
    """A frozen v4 pack missing ``security-policy.md`` stays NOT READY.

    The v4 manifest still requires the original eleven core files; only
    files introduced AFTER v4 (``execution-kernel.md``) are exempt.
    Removing ``security-policy.md`` is a regression and stays NOT READY.
    """
    from ralph.project_policy import validators

    workspace = MemoryWorkspace()
    # Bootstrap the v4 manifest MINUS security-policy.md.
    for name in markers.core_files_for_version("v4"):
        if name == "security-policy.md":
            continue
        path = f"{markers.CANONICAL_DIR}{name}"
        workspace.write(path, "<!-- ralph-policy-schema: freeze v4 -->\n")
    workspace.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    workspace.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )
    workspace.write(
        markers.PORTFOLIO_PATH,
        'schema_version = "v1"\ndefault_budget_seconds = 1.0\nkernel_controls = []\n',
    )
    stack = type(
        "S", (), {"primary_language": "Python", "secondary_languages": (), "frameworks": ()}
    )()
    findings = validators.validate_readiness(workspace, stack)
    paths = {finding.path for finding in findings}
    assert f"{markers.CANONICAL_DIR}security-policy.md" in paths, (
        f"frozen v4 pack missing security-policy.md must stay NOT READY; got findings at: {paths}"
    )


def test_agents_md_three_state_ready_template() -> None:
    """The v5 READY template reports the three readiness dimensions."""
    from ralph.project_policy import agents_md

    template = agents_md._AGENTS_READY_TEMPLATE.format(
        begin=markers.AGENTS_BLOCK_BEGIN,
        end=markers.AGENTS_BLOCK_END,
        canonical_dir=markers.CANONICAL_DIR,
    )
    assert "structure complete" in template
    assert "implementation verified" in template
    assert "outcome evidence reviewed" in template
    assert "structural READY is NOT semantic correctness" in template


def test_agents_md_selective_loading_template() -> None:
    """The READY template states the selective-applicability rule (B2)."""
    from ralph.project_policy import agents_md

    template = agents_md._AGENTS_READY_TEMPLATE.format(
        begin=markers.AGENTS_BLOCK_BEGIN,
        end=markers.AGENTS_BLOCK_END,
        canonical_dir=markers.CANONICAL_DIR,
    )
    assert "load only its effective bounded policy portfolio" in template
    assert "kernel" in template
    assert "selected profiles" in template


def test_claim_scope_phrases_constant_is_pinned() -> None:
    """The claim-scope phrase list is exactly the pinned set (B7)."""
    expected = ("enforced", "guaranteed", "ensures", "always", "prevents all", "all ")
    assert expected == markers.CLAIM_SCOPE_PHRASES


def test_shipped_portfolio_uses_v2_schema() -> None:
    """The shipped policy-portfolio.toml starter uses schema v2 with the new
    required fields. Frozen-v1 packs are accepted by ``core_files_for_version``
    but a fresh v5 pack ships the v2 manifest."""
    content = starters.read_starter(starters.PORTFOLIO_STARTER_NAME)
    assert 'schema_version = "v2"' in content
    assert "detects =" in content
    assert "retirement_trigger =" in content

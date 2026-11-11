from __future__ import annotations

import pytest

from ralph.project_policy import _guard_inventory, markers, portfolio, starters
from ralph.workspace.memory import MemoryWorkspace


def test_kernel_starter_is_registered_in_v5_manifest() -> None:
    assert "execution-kernel.md" in markers.CORE_POLICY_FILES
    assert "execution-kernel.md" in starters.STARTER_NAMES
    assert markers.SCHEMA_VERSION == "v6"


def test_kernel_starter_carries_v5_marker_and_id() -> None:
    content = starters.read_starter("execution-kernel.md")
    first_line = next(line for line in content.splitlines() if line.strip())
    assert first_line == markers.POLICY_SCHEMA_MARKER
    assert "<!-- ralph-policy-id: execution-kernel.md -->" in content


def test_kernel_starter_has_guard_inventory_section() -> None:
    content = starters.read_starter("execution-kernel.md")
    rows = _guard_inventory._parse_guard_inventory(content)
    assert rows, "execution-kernel.md must carry at least one guard row"
    for row in rows:
        for column in (
            "guard",
            "owner",
            "protected_outcome",
            "detected_condition",
            "claim_scope",
            "enforcement",
            "retirement_trigger",
        ):
            assert row[column], f"row missing {column!r}: {row}"


def test_kernel_starter_required_headings_are_known() -> None:
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
    content = starters.read_starter("execution-kernel.md")
    assert markers.STARTER_TEMPLATE_TOKEN in content


def test_kernel_required_headings_include_guard_inventory() -> None:
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
    content = starters.read_starter(starter_name)
    rows = _guard_inventory._parse_guard_inventory(content)
    assert rows, f"{starter_name} must carry at least one guard row"


def test_guard_inventory_parser_rejects_missing_section() -> None:
    findings = _guard_inventory._check_guard_inventory(
        "## Purpose and scope\n\nReal content.\n",
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )
    assert any("missing" in finding.requirement_id for finding in findings), findings


def test_guard_inventory_parser_rejects_malformed_table() -> None:
    body = "## Guard inventory\n\n| wrong | header |\n|---|\n"
    findings = _guard_inventory._check_guard_inventory(
        body,
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )
    assert any("malformed" in finding.requirement_id for finding in findings), findings


def test_guard_inventory_parser_rejects_broad_claim() -> None:
    body = (
        "## Guard inventory\n"
        "\n"
        "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |\n"
        "|---|---|---|---|---|---|---|\n"
        "| g | o | dependency direction enforced | missing import-lint | the lint report per file | deterministic: make test | retired |"
    )
    findings = _guard_inventory._check_guard_inventory(
        body,
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )
    assert any("claim-scope" in finding.requirement_id for finding in findings), findings


def test_guard_inventory_parser_accepts_mirrored_claim() -> None:
    body = (
        "## Guard inventory\n"
        "\n"
        "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |\n"
        "|---|---|---|---|---|---|---|\n"
        "| g | o | dependency direction enforced | enforced by import-lint on src/ | enforced per import-lint on src/ | deterministic: make test | retired |"
    )
    findings = _guard_inventory._check_guard_inventory(
        body,
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )
    assert findings == [], findings


def test_guard_inventory_parser_rejects_empty_cells() -> None:
    body = (
        "## Guard inventory\n"
        "\n"
        "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |\n"
        "|---|---|---|---|---|---|---|\n"
        "| g |  | some outcome | some detected | the report per file | deterministic: make test | some retirement |"
    )
    findings = _guard_inventory._check_guard_inventory(
        body,
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )
    assert any("empty-owner" in finding.requirement_id for finding in findings), findings


def test_portfolio_v2_requires_detects_field() -> None:
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
    from ralph.project_policy import validators

    workspace = MemoryWorkspace()
    for name in markers.core_files_for_version("v4"):
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
    requirement_ids = {finding.requirement_id for finding in findings}
    assert "execution-kernel.md" not in " ".join(requirement_ids), (
        f"frozen v4 manifest should not require execution-kernel.md; got: {requirement_ids}"
    )


def test_validators_frozen_v4_pack_missing_security_fails() -> None:
    from ralph.project_policy import validators

    workspace = MemoryWorkspace()
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
    expected = ("enforced", "guaranteed", "ensures", "always", "prevents all", "all ")
    assert expected == markers.CLAIM_SCOPE_PHRASES


def test_shipped_portfolio_uses_v3_schema() -> None:
    content = starters.read_starter(starters.PORTFOLIO_STARTER_NAME)
    assert 'schema_version = "v3"' in content
    assert "detects =" in content
    assert "enforcement =" in content
    assert "retirement_trigger =" in content

from __future__ import annotations

import re

import pytest

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, portfolio, preflight, starters, validators
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.authentic_v4_data import V4_PORTFOLIO
from tests.project_policy.test_frozen_version_specs import _build_frozen_v5_pack

STACK = ProjectStack(primary_language="Python")
BAN_TABLE_HEADING = "## Verifiable constructs and banned escape hatches"


def _ban_findings(findings: list) -> list:
    return [
        finding
        for finding in findings
        if finding.requirement_id == markers.BANNED_CONSTRUCT_UNCOVERED_FINDING_ID
    ]


def _strip_ban_table(ws: MemoryWorkspace) -> MemoryWorkspace:
    path = f"{markers.CANONICAL_DIR}clean-code-policy.md"
    ws.write(path, ws.read(path).replace(f"{BAN_TABLE_HEADING}\n", ""))
    return ws


def _refreeze(ws: MemoryWorkspace, version: str) -> MemoryWorkspace:
    for name in markers.CORE_POLICY_FILES:
        path = f"{markers.CANONICAL_DIR}{name}"
        if ws.exists(path):
            ws.write(path, ws.read(path).replace("freeze v5", f"freeze {version}"))
    return ws


def _synthetic_spec(requires_ban_table_coverage: bool) -> markers.PolicyVersionSpec:
    return markers.PolicyVersionSpec(
        version="v7",
        core_manifest=markers.CORE_POLICY_FILES,
        guard_inventory_header=None,
        portfolio_schema_version=("v2",),
        allowed_frozen=True,
        requires_ban_table_coverage=requires_ban_table_coverage,
    )


def test_ban_table_gate_reads_version_specs(monkeypatch: pytest.MonkeyPatch) -> None:
    for requires in (False, True):
        monkeypatch.setitem(
            markers.VERSION_SPECS, "v7", _synthetic_spec(requires_ban_table_coverage=requires)
        )
        ws = _refreeze(_strip_ban_table(_build_frozen_v5_pack()), "v7")
        findings = validators.validate_readiness(ws, STACK)
        ban = _ban_findings(findings)
        if requires:
            assert ban
        else:
            assert not ban


def test_ban_table_gate_exempts_only_specs_that_disable_it() -> None:
    for version, spec in markers.VERSION_SPECS.items():
        ws = _refreeze(_strip_ban_table(_build_frozen_v5_pack()), version)
        findings = validators.validate_readiness(ws, STACK)
        ban = _ban_findings(findings)
        if spec.requires_ban_table_coverage:
            assert ban, f"{version} spec requires ban table coverage; got no finding"
        else:
            assert not ban, f"{version} spec exempts ban table coverage; got: {ban}"


def test_ban_table_gate_fails_closed_for_unmodelled_freeze() -> None:
    ws = _refreeze(_strip_ban_table(_build_frozen_v5_pack()), "v3")
    findings = validators.validate_readiness(ws, STACK)
    assert _ban_findings(findings)


def test_portfolio_schema_version_ladder_is_ordered_union_of_specs() -> None:
    ladder = markers.portfolio_schema_version_ladder()
    union = frozenset(
        ver for spec in markers.VERSION_SPECS.values() for ver in spec.portfolio_schema_version
    )
    assert frozenset(ladder) == union
    assert len(set(ladder)) == len(ladder)
    numeric = [int(ver[1:]) for ver in ladder]
    assert numeric == sorted(numeric)


def test_portfolio_ladder_drives_parse_variants() -> None:
    assert frozenset(markers.portfolio_schema_version_ladder()) == (
        portfolio.SUPPORTED_PORTFOLIO_SCHEMA_VERSIONS
    )
    ladder = markers.portfolio_schema_version_ladder()
    first, second = ladder[0], ladder[1]

    portfolio.parse_portfolio_toml(
        V4_PORTFOLIO.replace('schema_version = "v1"', f'schema_version = "{first}"')
    )

    starter = starters.read_starter("policy-portfolio.toml")
    v2_content = starter.replace('schema_version = "v3"', f'schema_version = "{second}"')
    v2_content = re.sub(r'enforcement\s*=\s*"[^"]*"\n', "", v2_content)
    portfolio.parse_portfolio_toml(v2_content)

    current_without_enforcement = re.sub(r'enforcement\s*=\s*"[^"]*"\n', "", starter)
    with pytest.raises(portfolio.PortfolioError, match="enforcement"):
        portfolio.parse_portfolio_toml(current_without_enforcement)


def test_default_portfolio_schema_version_is_first_allowed() -> None:
    for version, spec in markers.VERSION_SPECS.items():
        assert (
            markers.default_portfolio_schema_version_for_version(version)
            == (spec.portfolio_schema_version[0])
        )
    current = markers.VERSION_SPECS[markers.SCHEMA_VERSION]
    assert (
        markers.default_portfolio_schema_version_for_version(None)
        == (current.portfolio_schema_version[0])
    )
    assert (
        markers.default_portfolio_schema_version_for_version("unmodelled")
        == (current.portfolio_schema_version[0])
    )


def test_frozen_core_pack_blocks_starter_seeding() -> None:
    ws = _build_frozen_v5_pack()
    missing = f"{markers.CANONICAL_DIR}security-policy.md"
    ws.delete(missing)
    preflight.seed_project_policy_starters(ws, STACK)
    assert not ws.exists(missing)


def test_unfrozen_workspace_seeds_portfolio_starter() -> None:
    ws = MemoryWorkspace()
    seeded = preflight.seed_project_policy_starters(ws, STACK)
    assert markers.PORTFOLIO_PATH in seeded
    assert ws.exists(markers.PORTFOLIO_PATH)


def test_freeze_marker_after_first_line_is_not_a_freeze() -> None:
    ws = MemoryWorkspace()
    body = starters.read_starter("testing-policy.md")
    body = body.replace(
        "# Testing Policy",
        "# Testing Policy\n\n<!-- ralph-policy-schema: freeze v5 -->",
        1,
    )
    ws.write(f"{markers.CANONICAL_DIR}testing-policy.md", body)
    seeded = preflight.seed_project_policy_starters(ws, STACK)
    assert markers.PORTFOLIO_PATH in seeded


def test_frozen_policy_version_reads_leading_marker_only() -> None:
    marker = "<!-- ralph-policy-schema: freeze v5 -->"
    assert markers.frozen_policy_version(f"{marker}\nbody") == 5
    assert markers.frozen_policy_version(f"<!-- ralph-policy-schema: v6 -->\n{marker}\n") is None
    assert markers.frozen_policy_version(f"  {marker}  \nbody") == 5
    assert markers.frozen_policy_version("") is None

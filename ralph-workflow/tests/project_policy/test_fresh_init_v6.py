from __future__ import annotations

import re
from typing import TYPE_CHECKING

import pytest

from ralph.cli.commands import init as init_module
from ralph.language_detector.models import ProjectStack
from ralph.project_policy import (
    markers,
    parse_portfolio_toml,
    preflight,
    starters,
    validators,
)
from ralph.workspace.fs import FsWorkspace
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = [pytest.mark.timeout_seconds(10)]

RALPH_TEST_CHANGE_REASONS: dict[str, str] = {
    "test_fresh_starters_follow_fill_model_no_ralph_audit_ids": "the premise-check rule is stated again as rule 7, so the execution-kernel starter ships two advisory guard rows, the non-inferable instruction-surface rule and the premise-check rule; the fill-model placeholder applies to the remaining guards and every advisory row must name its review trigger",
    "test_all_bundled_starters_comply_with_v6_schema_and_seven_columns": "the premise-check rule is stated again as rule 7, so the execution-kernel starter ships two advisory guard rows, the non-inferable instruction-surface rule and the premise-check rule; the fill-model placeholder applies to the remaining guards and every advisory row must name its review trigger",
    "test_fresh_cli_init_seeds_v6_policy_pack": "the premise-check rule is stated again as rule 7, so the execution-kernel starter ships two advisory guard rows, the non-inferable instruction-surface rule and the premise-check rule; the fill-model placeholder applies to the remaining guards and every advisory row must name its review trigger",
    "test_fresh_real_filesystem_init_seeds_v6_policy_pack": "the premise-check rule is stated again as rule 7, so the execution-kernel starter ships two advisory guard rows, the non-inferable instruction-surface rule and the premise-check rule; the fill-model placeholder applies to the remaining guards and every advisory row must name its review trigger",
}

ADVISORY_STARTER_GUARDS: frozenset[str] = frozenset(
    {
        "Non-inferable instruction surface",
        "Premise check",
    }
)


def _assert_starter_enforcement(row: dict[str, str], name: str) -> None:
    enf = row.get("enforcement", "")
    if row.get("guard") in ADVISORY_STARTER_GUARDS:
        assert enf.startswith("advisory: "), (
            f"Starter {name} guard {row.get('guard')!r} must ship with an advisory "
            f"enforcement, observed: {enf!r}"
        )
        assert "; review:" in enf, (
            f"Starter {name} guard {row.get('guard')!r} must ship an advisory "
            f"enforcement with a review trigger, observed: {enf!r}"
        )
        return
    assert enf == "deterministic: PROJECT-FACT-UNRESOLVED", (
        f"Starter {name} guard {row.get('guard')!r} must ship with "
        f"'deterministic: PROJECT-FACT-UNRESOLVED', observed: {enf!r}"
    )


def _stack() -> ProjectStack:
    return ProjectStack(primary_language="Python")


def _parse_guard_inventory_table(content: str) -> list[dict[str, str]]:
    lines = [line.strip() for line in content.splitlines()]
    header_idx = -1
    for i, line in enumerate(lines):
        if line.startswith("|"):
            cells = [c.strip().lower() for c in line.strip("|").split("|")]
            if len(cells) >= 2 and cells[0] == "guard" and "enforcement" in cells:
                header_idx = i
                break
    if header_idx == -1 or header_idx + 2 >= len(lines):
        return []
    headers = [c.strip().lower() for c in lines[header_idx].strip("|").split("|")]
    rows: list[dict[str, str]] = []
    for line in lines[header_idx + 2 :]:
        if not line.startswith("|"):
            break
        cells = [c.strip() for c in line.strip("|").split("|")]
        if len(cells) == len(headers):
            rows.append(dict(zip(headers, cells, strict=False)))
    return rows


def test_fresh_workspace_preflight_seeds_all_twelve_core_v6_starters() -> None:
    ws = MemoryWorkspace()
    result = preflight.run_policy_readiness_preflight(ws, _stack())

    assert result.requires_remediation()
    assert len(markers.CORE_POLICY_FILES) == 12

    for name in markers.CORE_POLICY_FILES:
        rel_path = f"{markers.CANONICAL_DIR}{name}"
        assert ws.exists(rel_path), f"Fresh init must seed core starter {name}"
        content = ws.read(rel_path)

        first_line = content.splitlines()[0].strip()
        assert first_line == markers.POLICY_SCHEMA_MARKER, (
            f"Starter {name} first line must be {markers.POLICY_SCHEMA_MARKER!r}, got {first_line!r}"
        )
        assert f"{markers.POLICY_ID_PREFIX} {name} -->" in content, (
            f"Starter {name} must carry canonical policy ID marker"
        )


def test_fresh_workspace_seeds_seven_column_guard_inventory_with_enforcement() -> None:
    ws = MemoryWorkspace()
    preflight.run_policy_readiness_preflight(ws, _stack())

    expected_header_lower = tuple(h.lower() for h in markers.GUARD_INVENTORY_HEADER)
    assert len(expected_header_lower) == 7
    assert "enforcement" in expected_header_lower

    for name in markers.CORE_POLICY_FILES:
        rel_path = f"{markers.CANONICAL_DIR}{name}"
        content = ws.read(rel_path)

        rows = _parse_guard_inventory_table(content)
        assert rows, f"Starter {name} must contain parseable guard inventory rows"

        for row in rows:
            assert "enforcement" in row, f"Starter {name} row missing enforcement key: {row}"
            assert row["enforcement"], f"Starter {name} row has empty enforcement: {row}"
            assert len(row) == 7, f"Starter {name} row must have 7 cells: {row}"
            for key, val in row.items():
                assert val.strip(), f"Starter {name} cell {key} must not be empty"


def test_fresh_starters_follow_fill_model_no_ralph_audit_ids() -> None:
    ws = MemoryWorkspace()
    preflight.run_policy_readiness_preflight(ws, _stack())

    banned_audit_tokens = (
        "audit_secret_scan",
        "audit_instruction_integrity",
        "audit_test_integrity",
        "audit_repo_structure",
        "audit_comments",
        "audit_di_seam",
    )

    for name in markers.CORE_POLICY_FILES:
        rel_path = f"{markers.CANONICAL_DIR}{name}"
        content = ws.read(rel_path)
        rows = _parse_guard_inventory_table(content)

        for row in rows:
            enf = row.get("enforcement", "")
            _assert_starter_enforcement(row, name)
            for banned in banned_audit_tokens:
                assert banned not in enf, (
                    f"Starter {name} pre-filled Ralph internal audit ID {banned!r} in enforcement"
                )


def test_fresh_workspace_seeds_portfolio_v3_with_enforcement() -> None:
    ws = MemoryWorkspace()
    preflight.run_policy_readiness_preflight(ws, _stack())

    assert ws.exists(markers.PORTFOLIO_PATH), "policy-portfolio.toml must be seeded"
    portfolio_content = ws.read(markers.PORTFOLIO_PATH)

    assert 'schema_version = "v3"' in portfolio_content
    portfolio = parse_portfolio_toml(portfolio_content)

    assert portfolio.schema_version == "v3"
    assert len(portfolio.controls) > 0

    for control in portfolio.controls:
        assert control.enforcement, (
            f"Control {control.id} in policy-portfolio.toml must declare enforcement"
        )
        assert control.detects, (
            f"Control {control.id} in policy-portfolio.toml must declare detects"
        )
        assert control.retirement_trigger, (
            f"Control {control.id} in policy-portfolio.toml must declare retirement_trigger"
        )


def test_all_bundled_starters_comply_with_v6_schema_and_seven_columns() -> None:
    starter_names = list(starters.iter_starter_names())
    assert len(starter_names) == 22

    for name in starter_names:
        content = starters.read_starter(name)
        first_line = content.splitlines()[0].strip()
        assert first_line == markers.POLICY_SCHEMA_MARKER, (
            f"Bundled starter {name} first line must be v6 marker, got {first_line!r}"
        )

        rows = _parse_guard_inventory_table(content)
        assert rows, f"Bundled starter {name} must have parseable guard inventory rows"
        for row in rows:
            assert len(row) == 7, f"Bundled starter {name} row must have 7 columns"
            _assert_starter_enforcement(row, name)


def test_fresh_cli_init_seeds_v6_policy_pack(
    monkeypatch: pytest.MonkeyPatch, tmp_path: Path
) -> None:
    xdg_dir = tmp_path / "xdg"
    xdg_dir.mkdir()
    monkeypatch.setenv("XDG_CONFIG_HOME", str(xdg_dir))
    monkeypatch.chdir(tmp_path)

    init_module.init_command(template=None)

    policy_dir = tmp_path / "docs" / "ralph-workflow-policy"
    assert policy_dir.is_dir(), "docs/ralph-workflow-policy must be created by ralph --init"

    portfolio_file = policy_dir / "policy-portfolio.toml"
    assert portfolio_file.is_file()
    portfolio = parse_portfolio_toml(portfolio_file.read_text(encoding="utf-8"))
    assert portfolio.schema_version == "v3"
    assert markers.PORTFOLIO_SCHEMA_VERSION == "v3"
    for ctrl in portfolio.controls:
        assert ctrl.enforcement is not None, f"Control {ctrl.id} missing enforcement"

    for name in markers.CORE_POLICY_FILES:
        policy_file = policy_dir / name
        assert policy_file.is_file(), f"Expected {name} to exist on disk after ralph --init"
        text = policy_file.read_text(encoding="utf-8")
        assert text.startswith("<!-- ralph-policy-schema: v6 -->")
        rows = _parse_guard_inventory_table(text)
        assert rows, f"Expected guard inventory rows in {name}"
        for r in rows:
            assert len(r) == 7, f"Row in {name} must have 7 columns"
            _assert_starter_enforcement(r, name)

    agents_file = tmp_path / "AGENTS.md"
    assert agents_file.is_file(), "AGENTS.md must be bootstrapped by ralph --init"
    agents_text = agents_file.read_text(encoding="utf-8")
    assert markers.AGENTS_BLOCK_BEGIN in agents_text
    assert markers.AGENTS_BLOCK_END in agents_text
    assert "execution-kernel.md first" in agents_text

    custom_text = "# Custom Kernel\n\nCustom user changes here.\n"
    kernel_path = policy_dir / "execution-kernel.md"
    kernel_path.write_text(custom_text, encoding="utf-8")

    init_module.init_command(template=None)

    assert kernel_path.read_text(encoding="utf-8") == custom_text
    assert agents_file.read_text(encoding="utf-8").count(markers.AGENTS_BLOCK_BEGIN) == 1


def test_fresh_real_filesystem_init_seeds_v6_policy_pack(tmp_path: Path) -> None:
    ws = FsWorkspace(tmp_path)
    stack = _stack()

    result = preflight.run_policy_readiness_preflight(ws, stack)
    assert result.requires_remediation()

    policy_dir = tmp_path / "docs" / "ralph-workflow-policy"
    assert policy_dir.is_dir()

    portfolio_file = policy_dir / "policy-portfolio.toml"
    assert portfolio_file.is_file()
    portfolio = parse_portfolio_toml(portfolio_file.read_text(encoding="utf-8"))
    assert portfolio.schema_version == "v3"

    for name in markers.CORE_POLICY_FILES:
        policy_file = policy_dir / name
        assert policy_file.is_file(), f"Expected {name} to exist on disk"
        text = policy_file.read_text(encoding="utf-8")
        assert text.startswith("<!-- ralph-policy-schema: v6 -->")
        rows = _parse_guard_inventory_table(text)
        assert rows, f"Expected guard inventory rows in {name}"
        for r in rows:
            _assert_starter_enforcement(r, name)

    second_result = preflight.run_policy_readiness_preflight(ws, stack)
    assert second_result.requires_remediation()
    core_policy_paths = {f"{markers.CANONICAL_DIR}{n}" for n in markers.CORE_POLICY_FILES}
    assert not any(p in core_policy_paths for p in second_result.changed_files)


def test_fresh_seeded_starter_resolves_via_fill_model_to_valid() -> None:
    starter_name = "gate-script-policy.md"
    content = starters.read_starter(starter_name)

    content = content.replace(
        "RALPH-FACT: supported_platforms: PROJECT-FACT-UNRESOLVED",
        "RALPH-FACT: supported_platforms: linux, macos",
    )
    content = content.replace(
        "RALPH-FACT: shell_dialect: PROJECT-FACT-UNRESOLVED",
        "RALPH-FACT: shell_dialect: bash",
    )
    content = content.replace(
        "RALPH-FACT: script_directory: PROJECT-FACT-UNRESOLVED",
        "RALPH-FACT: script_directory: scripts/",
    )
    content = content.replace(
        "RALPH-COMMAND: PROJECT-FACT-UNRESOLVED",
        "RALPH-COMMAND: shellcheck scripts/",
    )
    content = content.replace(
        "deterministic: PROJECT-FACT-UNRESOLVED",
        "deterministic: shellcheck scripts/",
    )
    content = re.sub(
        r"<!--\s*(RALPH-STARTER-TEMPLATE|REPLACE-ME):.*?-->\s*", "", content, flags=re.DOTALL
    )

    ws = MemoryWorkspace()
    preflight.run_policy_readiness_preflight(ws, _stack())
    findings_before = [
        f for f in validators.validate_readiness(ws, _stack()) if starter_name in f.path
    ]
    assert findings_before, f"Unresolved {starter_name} must produce findings"

    rel_path = f"{markers.CANONICAL_DIR}{starter_name}"
    ws.write(rel_path, content)

    findings_after = [
        f for f in validators.validate_readiness(ws, _stack()) if starter_name in f.path
    ]
    assert findings_after == [], (
        f"Resolved starter {starter_name} must have 0 findings: {findings_after}"
    )

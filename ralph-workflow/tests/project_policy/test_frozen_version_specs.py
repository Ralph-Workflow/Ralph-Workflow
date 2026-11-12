from __future__ import annotations

import re

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, portfolio, starters, validators
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.test_v4_corpus_authentic import build_v4_pack

RALPH_TEST_CHANGE_REASONS = {
    "test_frozen_v5_pack_validates_ready": (
        "the v5 pack drops the enforcement-status cell the v6 starter "
        "rule-to-enforcer ladder carries"
    ),
}


def test_version_specs_registry_invariants() -> None:
    assert "v4" in markers.VERSION_SPECS
    assert "v5" in markers.VERSION_SPECS

    v4_spec = markers.VERSION_SPECS["v4"]
    assert v4_spec.version == "v4"
    assert len(v4_spec.core_manifest) == 11
    assert "execution-kernel.md" not in v4_spec.core_manifest
    assert "testing-policy.md" in v4_spec.core_manifest
    assert v4_spec.guard_inventory_header is None
    assert v4_spec.guard_inventory_columns == 0
    assert v4_spec.portfolio_schema_version == ("v1", "v2")
    assert v4_spec.allowed_frozen is True

    v5_spec = markers.VERSION_SPECS["v5"]
    assert v5_spec.version == "v5"
    assert len(v5_spec.core_manifest) == 12
    assert "execution-kernel.md" in v5_spec.core_manifest
    assert v5_spec.guard_inventory_header == markers.guard_inventory_header_for_version("v5")
    assert v5_spec.guard_inventory_columns == 6
    assert v5_spec.portfolio_schema_version == ("v2",)
    assert v5_spec.allowed_frozen is True

    assert "v6" in markers.VERSION_SPECS
    v6_spec = markers.VERSION_SPECS["v6"]
    assert v6_spec.version == "v6"
    assert len(v6_spec.core_manifest) == 12
    assert v6_spec.guard_inventory_header == markers.GUARD_INVENTORY_HEADER
    assert v6_spec.guard_inventory_columns == 7
    assert v6_spec.portfolio_schema_version == ("v3",)
    assert v6_spec.allowed_frozen is True


def test_version_spec_helpers() -> None:
    v4_files = markers.core_files_for_version("v4")
    assert len(v4_files) == 11
    assert "execution-kernel.md" not in v4_files
    assert "security-policy.md" in v4_files
    assert markers.core_files_for_version("v5") == markers.CORE_POLICY_FILES
    assert markers.core_files_for_version(None) == markers.CORE_POLICY_FILES
    assert markers.core_files_for_version("unknown") == markers.CORE_POLICY_FILES

    assert markers.spec_for_version("v4").version == "v4"
    assert markers.spec_for_version("v5").version == "v5"
    assert markers.spec_for_version(None).version == markers.SCHEMA_VERSION

    assert markers.is_allowed_frozen_version("v4") is True
    assert markers.is_allowed_frozen_version("v5") is True
    assert markers.is_allowed_frozen_version("v99") is False

    assert markers.guard_inventory_header_for_version("v4") is None
    assert (
        markers.guard_inventory_header_for_version("v5")
        == markers.spec_for_version("v5").guard_inventory_header
    )
    assert markers.guard_inventory_header_for_version("v6") == markers.GUARD_INVENTORY_HEADER

    assert markers.portfolio_schema_version_for_version("v4") == "v1"
    assert markers.portfolio_schema_version_for_version("v5") == "v2"
    assert markers.portfolio_schema_version_for_version("v6") == "v3"

    supported_portfolios = markers.supported_portfolio_schema_versions()
    assert supported_portfolios == frozenset({"v1", "v2", "v3"})
    assert supported_portfolios == portfolio.SUPPORTED_PORTFOLIO_SCHEMA_VERSIONS


def test_frozen_v4_pack_with_portfolio_v2_validates_ready() -> None:
    ws = build_v4_pack()
    v2_content = starters.read_starter("policy-portfolio.toml").replace(
        'schema_version = "v3"', 'schema_version = "v2"'
    )
    v2_content = re.sub(r'enforcement\s*=\s*"[^"]*"\n', "", v2_content)
    ws.write(markers.PORTFOLIO_PATH, v2_content)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert findings == []


def test_frozen_v4_pack_with_portfolio_v3_rejected() -> None:
    ws = build_v4_pack()
    v3_content = starters.read_starter("policy-portfolio.toml")
    ws.write(markers.PORTFOLIO_PATH, v3_content)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any("RWP-PORTFOLIO:invalid" in f.requirement_id for f in findings)


def test_frozen_v4_pack_missing_core_file_fails() -> None:
    ws = build_v4_pack(exclude_files=("security-policy.md",))
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any("security-policy.md" in f.requirement_id for f in findings)


def _clean_v5_starter_prose(body: str) -> str:
    body = re.sub(r"<!--\s*RALPH-STARTER-TEMPLATE:.*?-->\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"<!--\s*REPLACE-ME:.*?-->\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"RALPH-COMMAND:\s*PROJECT-FACT-UNRESOLVED", "RALPH-COMMAND: make test", body)
    body = body.replace("<date>", "2026-07-13")
    body = body.replace("<trigger>", "policy-change")
    body = body.replace("REPLACE-ME", "verified-token")
    body = body.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
    body = re.sub(
        r"<!-- ralph-policy-schema:\s*v\d+\s*-->", "<!-- ralph-policy-schema: freeze v5 -->", body
    )
    body = body.replace(
        "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Enforcement | Retirement trigger |",
        "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Retirement trigger |",
    )
    body = body.replace(
        "|---|---|---|---|---|---|---|",
        "|---|---|---|---|---|---|",
    )
    body = body.replace(" | deterministic: verified-value |", " |")
    body = re.sub(r" \| (advisory|deterministic):[^|]* \|", " |", body)
    return body


def _build_frozen_v5_pack(*, exclude_files: tuple[str, ...] = ()) -> MemoryWorkspace:
    ws = MemoryWorkspace()
    for name in markers.CORE_POLICY_FILES:
        if name in exclude_files:
            continue
        raw_body = starters.read_starter(name)
        cleaned_body = _clean_v5_starter_prose(raw_body)
        ws.write(f"{markers.CANONICAL_DIR}{name}", cleaned_body)

    ws.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    ws.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )
    v2_portfolio = starters.read_starter("policy-portfolio.toml").replace(
        'schema_version = "v3"', 'schema_version = "v2"'
    )
    v2_portfolio = re.sub(r'enforcement\s*=\s*"[^"]*"\n', "", v2_portfolio)
    ws.write(markers.PORTFOLIO_PATH, v2_portfolio)
    return ws


def test_frozen_v5_pack_validates_ready() -> None:
    ws = _build_frozen_v5_pack()
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert findings == []


def test_frozen_v5_pack_missing_execution_kernel_fails() -> None:
    ws = _build_frozen_v5_pack(exclude_files=("execution-kernel.md",))
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any("execution-kernel.md" in f.requirement_id for f in findings)


def test_frozen_v5_pack_malformed_guard_inventory_fails() -> None:
    ws = _build_frozen_v5_pack()
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ws.read(path)
    corrupted = content.replace("| Claim scope |", "")
    ws.write(path, corrupted)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any("testing-policy.md:malformed" in f.requirement_id for f in findings)


def test_freeze_outside_modelled_set_fails() -> None:
    ws = _build_frozen_v5_pack()
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ws.read(path).replace("freeze v5", "freeze v3")
    ws.write(path, content)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(
        f.requirement_id.startswith(markers.ID_MARKER_MISSING)
        and "testing-policy.md" in f.requirement_id
        for f in findings
    )

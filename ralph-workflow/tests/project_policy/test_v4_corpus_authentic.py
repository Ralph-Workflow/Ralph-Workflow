from __future__ import annotations

import re

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, portfolio, starters, validators
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.authentic_v4_data import V4_PORTFOLIO, V4_STARTERS

V4_CORE_FILES: tuple[str, ...] = markers._V4_CORE_FILES


def _clean_policy_prose(body: str) -> str:
    body = re.sub(r"<!--\s*RALPH-STARTER-TEMPLATE:.*?-->\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"<!--\s*REPLACE-ME:.*?-->\s*", "", body, flags=re.DOTALL)
    body = re.sub(r"RALPH-COMMAND:\s*PROJECT-FACT-UNRESOLVED", "RALPH-COMMAND: make test", body)
    body = body.replace("<date>", "2026-07-13")
    body = body.replace("<trigger>", "policy-change")
    body = body.replace("REPLACE-ME", "verified-token")
    body = body.replace("deterministic: PROJECT-FACT-UNRESOLVED", "deterministic: make test")
    body = body.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
    return body


def _clean_v4_policy(body: str) -> str:
    body = body.replace(
        "<!-- ralph-policy-schema: v4 -->",
        "<!-- ralph-policy-schema: freeze v4 -->",
        1,
    )
    return _clean_policy_prose(body)


_CLEAN_V4_STARTERS: dict[str, str] = {
    name: _clean_v4_policy(body) for name, body in V4_STARTERS.items()
}


def build_v4_pack(
    stack: ProjectStack | None = None,
    *,
    exclude_files: tuple[str, ...] = (),
    portfolio_content: str | None = V4_PORTFOLIO,
) -> MemoryWorkspace:
    ws = MemoryWorkspace()
    for name in V4_CORE_FILES:
        if name in exclude_files:
            continue
        body = _CLEAN_V4_STARTERS[name]
        path = f"{markers.CANONICAL_DIR}{name}"
        ws.write(path, body)

    ws.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    ws.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )
    if portfolio_content is not None:
        ws.write(markers.PORTFOLIO_PATH, portfolio_content)
    return ws


_build_v4_pack = build_v4_pack


def test_v4_starter_source_present_in_authentic_snapshot() -> None:
    for starter_name in V4_CORE_FILES:
        assert starter_name in V4_STARTERS, (
            f"v4 starter {starter_name!r} missing from authentic snapshot"
        )
        body = V4_STARTERS[starter_name]
        assert body, f"v4 starter {starter_name!r} is empty in authentic snapshot"
        assert "ralph-policy-schema: v4" in body, (
            f"v4 starter {starter_name!r} does not carry the v4 marker"
        )


def test_v4_pack_full_v4_manifest_validates() -> None:
    ws = _build_v4_pack()
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert findings == [], f"Complete frozen v4 pack must evaluate to READY; got: {findings}"


def test_v4_pack_missing_security_policy_fails() -> None:
    ws = _build_v4_pack(exclude_files=("security-policy.md",))
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert findings, "Frozen v4 pack missing security-policy.md must evaluate to NOT READY"
    missing_findings = [
        f
        for f in findings
        if f.path == f"{markers.CANONICAL_DIR}security-policy.md"
        and f.requirement_id == f"{markers.ID_CORE_MISSING}:security-policy.md"
    ]
    assert missing_findings, (
        f"Expected {markers.ID_CORE_MISSING}:security-policy.md finding; got: {findings}"
    )


def test_v4_pack_missing_portfolio_fails() -> None:
    ws = _build_v4_pack(portfolio_content=None)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert findings, "Frozen v4 pack missing portfolio must evaluate to NOT READY"
    portfolio_missing = [
        f
        for f in findings
        if f.path == markers.PORTFOLIO_PATH
        and f.requirement_id == f"{markers.ID_PORTFOLIO}:missing"
    ]
    assert portfolio_missing, f"Expected {markers.ID_PORTFOLIO}:missing finding; got: {findings}"


def test_v4_pack_malformed_portfolio_fails() -> None:
    ws = _build_v4_pack(portfolio_content='schema_version = "v1"\nkernel_controls = 123\n')
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert findings, "Frozen v4 pack with malformed portfolio must evaluate to NOT READY"
    portfolio_invalid = [
        f
        for f in findings
        if f.path == markers.PORTFOLIO_PATH
        and f.requirement_id == f"{markers.ID_PORTFOLIO}:invalid"
    ]
    assert portfolio_invalid, f"Expected {markers.ID_PORTFOLIO}:invalid finding; got: {findings}"


def test_v4_pack_does_not_require_execution_kernel() -> None:
    ws = _build_v4_pack()
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert not any(f.path == f"{markers.CANONICAL_DIR}execution-kernel.md" for f in findings), (
        f"frozen v4 pack should not require execution-kernel.md; got: {findings}"
    )


def test_v4_pack_does_not_require_v2_portfolio() -> None:
    ws = _build_v4_pack()
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    v2_only_findings = [
        f
        for f in findings
        if f.path == markers.PORTFOLIO_PATH
        and (
            "schema_version must be v2" in f.missing_evidence
            or "detects" in f.missing_evidence
            or "retirement_trigger" in f.missing_evidence
        )
    ]
    assert not v2_only_findings, (
        f"frozen v4 pack rejected for v2 portfolio fields: {v2_only_findings}"
    )


def test_fresh_v6_pack_validates_against_v6_manifest() -> None:
    ws = MemoryWorkspace()
    for name in markers.CORE_POLICY_FILES:
        body = starters.read_starter(name)
        cleaned = _clean_policy_prose(body)
        path = f"{markers.CANONICAL_DIR}{name}"
        ws.write(path, cleaned)
    ws.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )
    ws.write(
        markers.CLAUDE_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee AGENTS.md.\n{markers.AGENTS_BLOCK_END}\n",
    )
    shipped_portfolio = _clean_policy_prose(starters.read_starter("policy-portfolio.toml"))
    ws.write(markers.PORTFOLIO_PATH, shipped_portfolio)

    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert findings == [], f"fresh v6 pack must evaluate to READY; got: {findings}"

    parsed = portfolio.parse_portfolio_toml(shipped_portfolio)
    assert parsed.schema_version == "v3"


def test_frozen_v4_pack_missing_required_heading_fails() -> None:
    ws = _build_v4_pack()
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ws.read(path).replace("## Purpose and scope", "## Wrong heading", 1)
    ws.write(path, content)
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(f.path == path and "Purpose and scope" in f.requirement_id for f in findings), (
        f"Expected missing heading finding for Purpose and scope in frozen v4 pack; got: {findings}"
    )


def test_frozen_v4_pack_missing_required_fact_fails() -> None:
    ws = _build_v4_pack()
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    lines = [line for line in ws.read(path).splitlines() if "test_command" not in line]
    ws.write(path, "\n".join(lines))
    findings = validators.validate_readiness(ws, ProjectStack(primary_language="Python"))
    assert any(f.path == path and "fact-test_command" in f.requirement_id for f in findings), (
        f"Expected missing fact finding in frozen v4 pack; got: {findings}"
    )

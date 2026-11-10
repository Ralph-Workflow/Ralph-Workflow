"""Contract tests for finding severity (blocking vs non-blocking warnings)."""

from __future__ import annotations

from datetime import date

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import (
    agents_md,
    cache,
    instruction_surface,
    markers,
    preflight,
    remediation,
    validators,
)
from ralph.project_policy.models import PolicyFinding, ReadinessStatus
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus


def _stack() -> ProjectStack:
    return ProjectStack(primary_language="Python")


def test_finding_defaults_to_blocking() -> None:
    finding = PolicyFinding(
        requirement_id="RWP-TEST:BLOCKING",
        path="test.md",
        missing_evidence="missing",
        required_outcome="fix",
    )
    assert finding.blocking is True
    d = finding.to_dict()
    assert d["blocking"] is True


def test_finding_can_be_non_blocking() -> None:
    finding = PolicyFinding(
        requirement_id="RWP-TEST:WARNING",
        path="test.md",
        missing_evidence="missing",
        required_outcome="fix",
        blocking=False,
    )
    assert finding.blocking is False
    d = finding.to_dict()
    assert d["blocking"] is False


def test_line_budget_findings_are_non_blocking() -> None:
    # 201 lines triggers PROJECT_AGENTS_MD_LINE_BUDGET
    long_content = "# Title\n" + "\n".join(f"line {i}" for i in range(205))
    findings = agents_md.check_agents_md_line_budgets(long_content)
    assert len(findings) == 1
    assert findings[0].requirement_id == markers.PROJECT_AGENTS_MD_LINE_BUDGET_FINDING_ID
    assert findings[0].blocking is False

    # Managed block exceeding 40 lines
    managed_lines = [markers.AGENTS_BLOCK_BEGIN]
    managed_lines.extend(f"instruction {i}" for i in range(45))
    managed_lines.append(markers.AGENTS_BLOCK_END)
    block_content = "\n".join(managed_lines)
    findings2 = agents_md.check_agents_md_line_budgets(block_content)
    assert any(
        f.requirement_id == markers.MANAGED_BLOCK_LINE_BUDGET_FINDING_ID and not f.blocking
        for f in findings2
    )


def test_research_freshness_finding_is_non_blocking() -> None:
    clock_date = date(2026, 11, 10)
    old_date = date(2024, 1, 1)  # > 365 days old
    validators.set_research_freshness_clock(lambda: clock_date)
    try:
        policy_body = f"""<!-- ralph-policy-schema: v6 -->
# Test Policy

## Research basis
- publisher: Example Corp
  title: Example study
  http: https://example.com/study
  review date: {old_date.isoformat()}
"""
        findings = validators.check_policy_citations(policy_body, "docs/test-policy.md")
        freshness_findings = [
            f for f in findings if markers.RESEARCH_FRESHNESS_FINDING_ID in f.requirement_id
        ]
        assert len(freshness_findings) == 1
        assert freshness_findings[0].blocking is False
    finally:
        validators.set_research_freshness_clock(None)


def test_structural_and_security_findings_stay_blocking() -> None:
    # Hidden unicode stays blocking
    unicode_findings = instruction_surface.check_hidden_unicode("hello\u200bworld", "test.md")
    assert len(unicode_findings) == 1
    assert unicode_findings[0].blocking is True

    # Missing cited path stays blocking
    ws = MemoryWorkspace()
    cited_findings = agents_md.check_agents_md_path_citations(
        ws,
        "# AGENTS.md\n- [missing](file:///nonexistent/path.md)\n",
    )
    assert len(cited_findings) == 1
    assert cited_findings[0].requirement_id == markers.AGENTS_MD_CITED_PATH_FINDING_ID
    assert cited_findings[0].blocking is True


def test_workspace_with_only_line_budget_warning_assesses_ready() -> None:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    # Add extra lines to AGENTS.md to exceed 200-line budget
    current = ws.read(markers.AGENTS_MD)
    extra_lines = "\n".join(f"# comment line {i}" for i in range(210))
    ws.write(markers.AGENTS_MD, current + "\n" + extra_lines)

    all_findings = validators.validate_readiness(ws, _stack())
    assert len(all_findings) >= 1
    assert all(not f.blocking for f in all_findings)

    # Preflight must return READY even with warnings present
    result = preflight.run_policy_readiness_preflight(ws, _stack())
    assert result.is_ready()
    assert result.status is ReadinessStatus.READY
    # Warnings are still reported on the result
    assert len(result.findings) >= 1


def test_remediation_prompt_labels_severity() -> None:
    ws = MemoryWorkspace()
    blocking = PolicyFinding(
        requirement_id="RWP-TEST:BLOCK",
        path="block.md",
        missing_evidence="missing",
        required_outcome="fix",
        blocking=True,
    )
    warning = PolicyFinding(
        requirement_id="RWP-TEST:WARN",
        path="warn.md",
        missing_evidence="stale",
        required_outcome="update",
        blocking=False,
    )
    captured_prompt: list[str] = []

    def fake_invoke(*, phase: str, prompt_path: str) -> bool:
        del phase
        captured_prompt.append(ws.read(prompt_path))
        return True

    remediation.run_remediation_phase(
        ws,
        _stack(),
        [blocking, warning],
        invoke_agent=fake_invoke,
    )
    assert len(captured_prompt) == 1
    assert "severity: blocking" in captured_prompt[0]
    assert "severity: warning" in captured_prompt[0]


def test_cache_never_stores_warnings_as_failures() -> None:
    ws = MemoryWorkspace()
    # Cache only writes for ReadinessStatus.READY
    cache.write_cache(ws, _stack(), ReadinessStatus.REMEDIATION_REQUIRED)
    assert not ws.exists(markers.CACHE_REL_PATH)

    cache.write_cache(ws, _stack(), ReadinessStatus.BLOCKED)
    assert not ws.exists(markers.CACHE_REL_PATH)

    cache.write_cache(ws, _stack(), ReadinessStatus.READY)
    assert ws.exists(markers.CACHE_REL_PATH)
    assert cache.read_cached_ready(ws, _stack()) is True

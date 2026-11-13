from __future__ import annotations

from datetime import date
from pathlib import Path

from ralph.project_policy import (
    agents_md,
    check_policy_citations,
    get_research_freshness_clock,
    instruction_surface,
    markers,
    set_research_freshness_clock,
    validators,
)
from ralph.testing.audit_instruction_integrity import (
    CATEGORY_HIDDEN_UNICODE,
    RULE_HIDDEN_UNICODE,
    audit_instruction_integrity,
)
from ralph.workspace.memory import MemoryWorkspace
from tests.project_policy.policy_corpus import seed_complete_corpus, stack


def test_managed_block_line_budget_passes_at_or_below_limit() -> None:
    block_lines = ["Line inside block"] * 38
    content = (
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        + "\n".join(block_lines)
        + f"\n{markers.AGENTS_BLOCK_END}\n"
    )
    findings = agents_md.check_agents_md_line_budgets(content)
    assert not any(
        f.requirement_id == markers.MANAGED_BLOCK_LINE_BUDGET_FINDING_ID for f in findings
    )


def test_managed_block_line_budget_fires_above_limit() -> None:
    block_lines = ["Line inside block"] * 41
    content = (
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        + "\n".join(block_lines)
        + f"\n{markers.AGENTS_BLOCK_END}\n"
    )
    findings = agents_md.check_agents_md_line_budgets(content)
    matched = [
        f for f in findings if f.requirement_id == markers.MANAGED_BLOCK_LINE_BUDGET_FINDING_ID
    ]
    assert len(matched) == 1
    assert (
        "43 lines" in matched[0].missing_evidence
        or "41 lines" in matched[0].missing_evidence
        or "budget 40" in matched[0].missing_evidence
    )


def test_project_agents_md_line_budget_passes_at_or_below_limit() -> None:
    lines = ["# Title", ""] + [f"Rule {i}" for i in range(195)]
    content = "\n".join(lines) + "\n"
    findings = agents_md.check_agents_md_line_budgets(content)
    assert not any(
        f.requirement_id == markers.PROJECT_AGENTS_MD_LINE_BUDGET_FINDING_ID for f in findings
    )


def test_project_agents_md_line_budget_fires_above_limit() -> None:
    lines = ["# Title", ""] + [f"Rule {i}" for i in range(210)]
    content = "\n".join(lines) + "\n"
    findings = agents_md.check_agents_md_line_budgets(content)
    matched = [
        f for f in findings if f.requirement_id == markers.PROJECT_AGENTS_MD_LINE_BUDGET_FINDING_ID
    ]
    assert len(matched) == 1
    assert "200-line budget" in matched[0].missing_evidence


def test_agents_md_path_citations_passes_when_path_exists() -> None:
    ws = MemoryWorkspace()
    ws.write("docs/guide.md", "# Guide\n")
    ws.write("scripts/run.sh", "#!/bin/sh\n")
    content = "# Instructions\n\nSee [Guide](docs/guide.md) and run `scripts/run.sh`.\n"
    findings = agents_md.check_agents_md_path_citations(ws, content)
    assert findings == []


def test_agents_md_path_citations_fires_on_missing_path() -> None:
    ws = MemoryWorkspace()
    content = "# Instructions\n\nSee [Missing](docs/nonexistent.md) and `tools/ghost.py`.\n"
    findings = agents_md.check_agents_md_path_citations(ws, content)
    assert len(findings) == 2
    assert all(f.requirement_id == markers.AGENTS_MD_CITED_PATH_FINDING_ID for f in findings)
    assert any("docs/nonexistent.md" in f.missing_evidence for f in findings)
    assert any("tools/ghost.py" in f.missing_evidence for f in findings)


def test_agents_md_path_citations_detects_bare_filenames() -> None:
    ws = MemoryWorkspace()
    ws.write("PROMPT.md", "# Existing\n")
    content = "# Instructions\n\nSee `MISSING.md` and `PROMPT.md`.\n"
    findings = agents_md.check_agents_md_path_citations(ws, content)
    assert len(findings) == 1
    assert findings[0].requirement_id == markers.AGENTS_MD_CITED_PATH_FINDING_ID
    assert "MISSING.md" in findings[0].missing_evidence


def test_agents_md_path_citations_ignores_external_urls_and_git_refs() -> None:
    ws = MemoryWorkspace()
    content = (
        "# References\n\n"
        "Visit [Site](https://example.com/foo.py) or check `origin/main`.\n"
        "Also git branch `main`.\n"
    )
    findings = agents_md.check_agents_md_path_citations(ws, content)
    assert findings == []


def test_find_hidden_unicode_detects_zero_width_and_bidi_characters() -> None:
    dirty_text = "clean line\nsecret\u200bhidden\noverride\u202eRTL\n"
    occurrences = instruction_surface.find_hidden_unicode(dirty_text)
    assert len(occurrences) == 2
    assert occurrences[0] == (2, 7, "U+200B")
    assert occurrences[1] == (3, 9, "U+202E")


def test_find_hidden_unicode_detects_word_joiner_and_arabic_letter_mark() -> None:
    dirty_text = "word\u2060join\narabic\u061cmark\n"
    occurrences = instruction_surface.find_hidden_unicode(dirty_text)
    assert len(occurrences) == 2
    assert occurrences[0] == (1, 5, "U+2060")
    assert occurrences[1] == (2, 7, "U+061C")


def test_check_hidden_unicode_fires_for_new_characters() -> None:
    for code_point in ("\u2060", "\u061c"):
        findings = instruction_surface.check_hidden_unicode(
            f"text {code_point} hidden", "docs/t.md"
        )
        assert len(findings) == 1
        assert f"U+{ord(code_point):04X}" in findings[0].missing_evidence


def test_check_hidden_unicode_passes_on_multilingual_text() -> None:
    clean = (
        "Arabic: هذا نص عربي عادي\n"
        "CJK: これは普通の日本語です 中文文本\n"
        "Accented: àéîöü ñ ç\n"
        "Emoji: 🎉\n"
    )
    assert instruction_surface.find_hidden_unicode(clean) == []
    assert instruction_surface.check_hidden_unicode(clean, "docs/i18n.md") == []


def test_check_hidden_unicode_fires_policy_finding() -> None:
    dirty = "Policy with \ufeff BOM"
    findings = instruction_surface.check_hidden_unicode(dirty, "docs/test.md")
    assert len(findings) == 1
    assert "U+FEFF" in findings[0].missing_evidence
    assert findings[0].path == "docs/test.md"


def test_check_hidden_unicode_passes_on_clean_text() -> None:
    clean = "# Normal markdown\n\nAll standard ASCII and UTF-8 printable: àéîö.\n"
    assert instruction_surface.check_hidden_unicode(clean, "docs/test.md") == []


def test_research_freshness_passes_within_365_days() -> None:
    set_research_freshness_clock(lambda: date(2026, 11, 9))
    try:
        content = (
            "## Research basis\n\n"
            "- publisher: Official Org\n"
            "  title: Valid Policy\n"
            "  http: https://example.org/policy\n"
            "  review date: 2026-08-01\n"
        )
        findings = check_policy_citations(content, "docs/policy.md")
        assert not any(
            f.requirement_id.startswith(markers.RESEARCH_FRESHNESS_FINDING_ID) for f in findings
        )
    finally:
        set_research_freshness_clock(None)


def test_research_freshness_fires_when_older_than_365_days() -> None:
    set_research_freshness_clock(lambda: date(2026, 11, 9))
    try:
        content = (
            "## Research basis\n\n"
            "- publisher: Ancient Org\n"
            "  title: Old Policy\n"
            "  http: https://example.org/old\n"
            "  review date: 2024-01-01\n"
        )
        findings = check_policy_citations(content, "docs/policy.md")
        freshness_findings = [
            f
            for f in findings
            if f.requirement_id.startswith(markers.RESEARCH_FRESHNESS_FINDING_ID)
        ]
        assert len(freshness_findings) == 1
        assert "freshness budget" in freshness_findings[0].missing_evidence
    finally:
        set_research_freshness_clock(None)


def test_research_freshness_clock_injection_restores_default() -> None:
    def custom_clock() -> date:
        return date(2030, 1, 1)

    set_research_freshness_clock(custom_clock)
    assert get_research_freshness_clock() is custom_clock
    set_research_freshness_clock(None)
    assert get_research_freshness_clock() is not custom_clock


def test_instruction_integrity_audit_finds_hidden_unicode(tmp_path: Path) -> None:
    prompts_dir = tmp_path / "ralph" / "prompts" / "templates"
    prompts_dir.mkdir(parents=True)
    dirty_template = prompts_dir / "test.jinja"
    dirty_template.write_text("Hello \u200b World", encoding="utf-8")

    findings = audit_instruction_integrity(tmp_path)
    unicode_findings = [f for f in findings if f.rule == RULE_HIDDEN_UNICODE]
    assert len(unicode_findings) == 1
    assert unicode_findings[0].category == CATEGORY_HIDDEN_UNICODE
    assert "U+200B" in unicode_findings[0].detail


def test_research_freshness_boundary_is_silent_at_365_days_fires_at_366() -> None:
    content = (
        "## Research basis\n\n"
        "- publisher: Boundary Org\n"
        "  title: Boundary Policy\n"
        "  http: https://example.org/boundary\n"
        "  review date: 2026-01-01\n"
    )
    try:
        set_research_freshness_clock(lambda: date(2027, 1, 1))
        findings_at_boundary = check_policy_citations(content, "docs/policy.md")
        assert not any(
            f.requirement_id.startswith(markers.RESEARCH_FRESHNESS_FINDING_ID)
            for f in findings_at_boundary
        )

        set_research_freshness_clock(lambda: date(2027, 1, 2))
        findings_past_boundary = check_policy_citations(content, "docs/policy.md")
        freshness = [
            f
            for f in findings_past_boundary
            if f.requirement_id.startswith(markers.RESEARCH_FRESHNESS_FINDING_ID)
        ]
        assert len(freshness) == 1
        assert freshness[0].blocking is False
    finally:
        set_research_freshness_clock(None)


def test_instruction_integrity_audit_hidden_unicode_covers_all_file_classes(
    tmp_path: Path,
) -> None:
    (tmp_path / "AGENTS.md").write_text("managed block with \u200b hidden char\n", encoding="utf-8")
    policy_dir = tmp_path / "docs" / "ralph-workflow-policy"
    policy_dir.mkdir(parents=True)
    (policy_dir / "testing-policy.md").write_text("policy with \u202e bidi\n", encoding="utf-8")
    (tmp_path / ".mcp.json").write_text(
        '{"mcpServers": {"srv": {"command": "srv@1.2.3\u00ad"}}}\n',
        encoding="utf-8",
    )
    prompts_dir = tmp_path / "ralph" / "prompts" / "templates"
    prompts_dir.mkdir(parents=True)
    (prompts_dir / "task.jinja").write_text("prompt with \ufeff bom\n", encoding="utf-8")

    findings = audit_instruction_integrity(tmp_path)
    unicode_findings = [f for f in findings if f.rule == RULE_HIDDEN_UNICODE]
    flagged_files = {f.file_path for f in unicode_findings}
    assert "AGENTS.md" in flagged_files
    assert "docs/ralph-workflow-policy/testing-policy.md" in flagged_files
    assert ".mcp.json" in flagged_files
    assert "ralph/prompts/templates/task.jinja" in flagged_files


def test_validate_readiness_nonexistent_cited_path_is_blocking_silent_for_existing_path() -> None:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)
    ws.write("docs/existing-runbook.md", "# Runbook\n")
    agents_base = ws.read(markers.AGENTS_MD)

    ws.write(markers.AGENTS_MD, agents_base + "\nSee [Runbook](docs/existing-runbook.md).\n")
    findings = validators.validate_readiness(ws, stack())
    cited_findings = [
        f for f in findings if f.requirement_id == markers.AGENTS_MD_CITED_PATH_FINDING_ID
    ]
    assert cited_findings == []

    ws.write(markers.AGENTS_MD, agents_base + "\nSee [Missing](docs/nonexistent-runbook.md).\n")
    findings = validators.validate_readiness(ws, stack())
    cited_findings = [
        f for f in findings if f.requirement_id == markers.AGENTS_MD_CITED_PATH_FINDING_ID
    ]
    assert len(cited_findings) == 1
    assert cited_findings[0].blocking is True
    assert "docs/nonexistent-runbook.md" in cited_findings[0].missing_evidence


def test_validate_readiness_line_budget_fires_above_threshold_silent_below_non_blocking() -> None:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)

    findings_below = validators.validate_readiness(ws, stack())
    budget_findings_below = [
        f
        for f in findings_below
        if f.requirement_id
        in (
            markers.PROJECT_AGENTS_MD_LINE_BUDGET_FINDING_ID,
            markers.MANAGED_BLOCK_LINE_BUDGET_FINDING_ID,
        )
    ]
    assert budget_findings_below == []

    long_content = (
        ws.read(markers.AGENTS_MD) + "\n" + "\n".join(f"# Section {i}" for i in range(210))
    )
    ws.write(markers.AGENTS_MD, long_content)
    findings_above = validators.validate_readiness(ws, stack())
    proj_budget_findings = [
        f
        for f in findings_above
        if f.requirement_id == markers.PROJECT_AGENTS_MD_LINE_BUDGET_FINDING_ID
    ]
    assert len(proj_budget_findings) == 1
    assert proj_budget_findings[0].blocking is False

    ws_block = MemoryWorkspace()
    seed_complete_corpus(ws_block)
    long_block = (
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        f"See {markers.CANONICAL_DIR}.\n"
        + "\n".join(f"# Instruction line {i}" for i in range(45))
        + f"\n{markers.AGENTS_BLOCK_END}\n"
    )
    ws_block.write(markers.AGENTS_MD, long_block)
    findings_block = validators.validate_readiness(ws_block, stack())
    block_budget_findings = [
        f
        for f in findings_block
        if f.requirement_id == markers.MANAGED_BLOCK_LINE_BUDGET_FINDING_ID
    ]
    assert len(block_budget_findings) == 1
    assert block_budget_findings[0].blocking is False


def test_validate_readiness_freshness_fires_beyond_365_days_silent_within_non_blocking() -> None:
    ws = MemoryWorkspace()
    seed_complete_corpus(ws)

    set_research_freshness_clock(lambda: date(2026, 8, 1))
    try:
        findings_within = validators.validate_readiness(ws, stack())
        freshness_within = [
            f
            for f in findings_within
            if f.requirement_id.startswith(markers.RESEARCH_FRESHNESS_FINDING_ID)
        ]
        assert freshness_within == []

        set_research_freshness_clock(lambda: date(2028, 1, 1))
        findings_beyond = validators.validate_readiness(ws, stack())
        freshness_beyond = [
            f
            for f in findings_beyond
            if f.requirement_id.startswith(markers.RESEARCH_FRESHNESS_FINDING_ID)
        ]
        assert len(freshness_beyond) > 0
        assert all(f.blocking is False for f in freshness_beyond)
    finally:
        set_research_freshness_clock(None)

"""Tests for the deterministic project-policy validator."""

from __future__ import annotations

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, starters, validators
from ralph.workspace.memory import MemoryWorkspace


def _stack_with(primary="Python", secondary=(), frameworks=()):
    return ProjectStack(
        primary_language=primary,
        secondary_languages=list(secondary),
        frameworks=list(frameworks),
    )


def _complete_policy_body(*, filename: str, lang: str | None = None) -> str:
    """Build a policy body that satisfies every structural check (no placeholder)."""
    lines = [
        markers.POLICY_SCHEMA_MARKER,
        f"{markers.POLICY_ID_PREFIX} {filename} -->",
        "",
        "# Title",
        "",
        "## Purpose and scope",
        "Real content.",
        "",
        "## Default requirements",
        "Real content.",
        "",
        "## Project facts to resolve",
        "Real content.",
        "",
        "## AI execution instructions",
        "Real content.",
        "",
        "## Verification",
        "Real content.",
    ]
    if filename == "verification-policy.md":
        lines.append("")
        lines.append("## Bypass detection")
        lines.append("Real content.")
        lines.append("RALPH-COMMAND: echo bypass-audit")
    lines.append("")
    lines.append("## Exceptions")
    lines.append("Real content.")
    lines.append("")
    lines.append("## Maintenance triggers")
    lines.append("Real content.")
    lines.append("")
    lines.append("## Research basis")
    lines.append("")
    lines.append("- publisher: Test Publisher")
    lines.append("  title: Test Title")
    lines.append("  http: https://example.com")
    lines.append("  review date: 2026-07-11")
    lines.append("")
    lines.append("## Ralph markers")
    lines.append("Real content.")
    lines.append("")
    lines.append(markers.COMPLETION_MARKER)
    lines.append("")
    if lang is not None:
        lines.append(f"RALPH-LANG: {lang}")
        lines.append("RALPH-COMMAND: echo ok")
    else:
        lines.append("RALPH-COMMAND: echo ok")
    return "\n".join(lines)


def _seed_all_core_complete(workspace: MemoryWorkspace, stack: ProjectStack) -> None:
    """Seed every core + required-conditional file as fully complete."""
    workspace.mkdirs(markers.CANONICAL_DIR.rstrip("/"))
    for filename in markers.CORE_POLICY_FILES:
        workspace.write(
            f"{markers.CANONICAL_DIR}{filename}",
            _complete_policy_body(filename=filename, lang=None if filename not in {"typechecking-policy.md", "linting-policy.md"} else "Python"),
        )


def _seed_agents_md(workspace: MemoryWorkspace) -> None:
    workspace.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        f"See {markers.CANONICAL_DIR}.\n"
        f"{markers.AGENTS_BLOCK_END}\n",
    )


def _seed_claude_md(workspace: MemoryWorkspace) -> None:
    workspace.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee AGENTS.md for project policy.\n")


def test_no_findings_on_fully_populated_project() -> None:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    findings = validators.validate_readiness(ws, _stack_with())
    assert findings == []


def test_agents_md_missing_emits_finding() -> None:
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    findings = validators.validate_readiness(ws, _stack_with())
    paths = [f.path for f in findings]
    assert markers.AGENTS_MD in paths


def test_missing_core_file_emits_finding() -> None:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    # Seed all but one
    _seed_all_core_complete(ws, _stack_with())
    ws.remove(f"{markers.CANONICAL_DIR}testing-policy.md")
    findings = validators.validate_readiness(ws, _stack_with())
    paths = [f.path for f in findings]
    assert f"{markers.CANONICAL_DIR}testing-policy.md" in paths


def test_heading_missing_emits_finding() -> None:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    # Remove one heading
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ws.read(path)
    content = content.replace("## Purpose and scope", "## Different heading")
    ws.write(path, content)
    findings = validators.validate_readiness(ws, _stack_with())
    paths = [f.path for f in findings]
    assert path in paths


def test_placeholder_token_emits_finding() -> None:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ws.read(path)
    content = content.replace("Real content.", "TODO real content")
    ws.write(path, content)
    findings = validators.validate_readiness(ws, _stack_with())
    assert any(f.path == path and "placeholder" in f.missing_evidence.lower() for f in findings)


def test_missing_command_and_inapplicable_emits_finding() -> None:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ws.read(path)
    # Strip the RALPH-COMMAND line
    lines = content.splitlines()
    lines = [line for line in lines if not line.startswith(markers.COMMAND_MARKER)]
    ws.write(path, "\n".join(lines) + "\n")
    findings = validators.validate_readiness(ws, _stack_with())
    assert any(f.path == path and f.requirement_id.startswith(markers.ID_CMD_UNUSABLE) for f in findings)


def test_completion_marker_missing_emits_finding() -> None:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ws.read(path).replace(markers.COMPLETION_MARKER, "")
    ws.write(path, content)
    findings = validators.validate_readiness(ws, _stack_with())
    assert any(f.path == path and f.requirement_id.startswith(markers.ID_COMPLETION_MISSING) for f in findings)


def test_per_language_coverage_required_for_secondary_language() -> None:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    # Stack has Python + TypeScript but neither policy declares TypeScript.
    stack = _stack_with(primary="Python", secondary=["TypeScript"])
    findings = validators.validate_readiness(ws, stack)
    ids = {f.requirement_id for f in findings}
    assert any(i.startswith(f"{markers.ID_LANG_COVERAGE}:typechecking-policy.md:TypeScript") for i in ids)
    assert any(i.startswith(f"{markers.ID_LANG_COVERAGE}:linting-policy.md:TypeScript") for i in ids)


def test_bypass_detection_section_required_for_verification_policy() -> None:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    path = f"{markers.CANONICAL_DIR}verification-policy.md"
    content = ws.read(path).replace("## Bypass detection", "## Different")
    ws.write(path, content)
    findings = validators.validate_readiness(ws, _stack_with())
    assert any(
        f.path == path and "bypass detection" in f.missing_evidence.lower()
        for f in findings
    )


def test_unresolved_migration_candidate_emits_finding() -> None:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    # Create a doc that looks like a policy-like content but is not migrated.
    ws.write(
        "docs/testing.md",
        "# Testing Policy\n\nSome content with a recognized heading.\n",
    )
    findings = validators.validate_readiness(ws, _stack_with())
    assert any(f.path == "docs/testing.md" for f in findings)


def test_resolved_migration_candidate_emits_no_finding() -> None:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    ws.write(
        "docs/testing.md",
        "# Testing Policy\n\n"
        + markers.MIGRATED_MARKER_TEMPLATE.format(target="testing-policy.md")
        + "\n",
    )
    findings = validators.validate_readiness(ws, _stack_with())
    assert all(f.path != "docs/testing.md" for f in findings)


def test_headings_only_never_ready() -> None:
    """A policy file with every heading but no completion marker or commands must fail."""
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    # Strip the completion marker AND the command from one policy.
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ws.read(path)
    lines = [
        line
        for line in content.splitlines()
        if not line.startswith(markers.COMMAND_MARKER)
        and line.strip() != markers.COMPLETION_MARKER
    ]
    ws.write(path, "\n".join(lines) + "\n")
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    # Both markers missing must produce findings.
    assert any(i.startswith(markers.ID_COMPLETION_MISSING) for i in ids)
    assert any(i.startswith(markers.ID_CMD_UNUSABLE) for i in ids)


def test_citation_field_missing_emits_finding() -> None:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ws.read(path)
    # Replace the citation block to be missing the URL field.
    content = content.replace("http: https://example.com", "url: missing")
    ws.write(path, content)
    findings = validators.validate_readiness(ws, _stack_with())
    assert any(
        f.path == path and f.requirement_id.startswith(markers.ID_CITATION_MISSING)
        for f in findings
    )


def test_design_system_required_emits_design_system_finding() -> None:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    # Stack has a UI framework but no design-system policy file.
    stack = _stack_with(secondary=["CSS"])
    findings = validators.validate_readiness(ws, stack)
    assert any(
        "design-system-policy.md" in f.path
        for f in findings
    )


def test_starter_content_fails_validation_until_customized() -> None:
    """A freshly-seeded starter has placeholders and no completion marker -> failing."""
    ws = MemoryWorkspace()
    starters.seed_starter_into(ws, "testing-policy.md")
    findings = validators.validate_readiness(ws, _stack_with())
    paths = [f.path for f in findings]
    assert f"{markers.CANONICAL_DIR}testing-policy.md" in paths

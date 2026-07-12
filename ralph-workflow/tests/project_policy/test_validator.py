"""Tests for the deterministic project-policy validator."""

from __future__ import annotations

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import markers, starters, validators
from ralph.workspace.memory import MemoryWorkspace


def _stack_with(
    primary: str = "Python",
    secondary: list[str] | tuple[str, ...] = (),
    frameworks: list[str] | tuple[str, ...] = (),
) -> ProjectStack:
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
    if filename == "security-policy.md":
        lines.append("")
        lines.append("## Threat surfaces")
        lines.append("Real content.")
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
    # Every complete policy must declare at least one resolved project
    # fact so the validator's no-fact gate cannot be silently satisfied by
    # the surrounding structural completeness.
    lines.append(f"RALPH-FACT: {filename}: path = docs/ralph-workflow-policy/{filename}")
    lines.append("RALPH-FACT: owner = test-owner")
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


def test_removed_fact_lines_emits_no_fact_finding_per_policy() -> None:
    """AC-05/AC-08: removing every RALPH-FACT line from a complete policy MUST
    surface a stable :data:`RWP-PLACEHOLDER:<filename>:no-fact` finding and
    the project MUST NOT reach READY. This is the regression for the
    analyzer-found defect where a fully-structured file with no
    machine-checkable project facts still validated as complete.
    """
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    # Strip every RALPH-FACT line from every core policy.
    stripped_paths: list[str] = []
    for filename in markers.CORE_POLICY_FILES:
        path = f"{markers.CANONICAL_DIR}{filename}"
        content = ws.read(path)
        lines = [
            line
            for line in content.splitlines()
            if not line.startswith(markers.FACT_MARKER)
        ]
        ws.write(path, "\n".join(lines) + "\n")
        stripped_paths.append(path)
    findings = validators.validate_readiness(ws, _stack_with())
    ids_by_path = {(f.path, f.requirement_id) for f in findings}
    for path in stripped_paths:
        # Stable id suffix ``:no-fact`` so the user can grep for it; the
        # analyzer's repro produced exactly this missing finding for each
        # affected policy.
        assert (path, f"{markers.ID_PLACEHOLDER}:{path.split('/')[-1]}:no-fact") in ids_by_path, (
            f"missing RWP-PLACEHOLDER:no-fact finding for {path}; "
            f"observed ids: {[i for p, i in ids_by_path if p == path]}"
        )


def test_empty_general_inapplicable_emits_finding() -> None:
    """AC-06: an empty ``RALPH-INAPPLICABLE:`` line at file scope MUST be
    rejected by the validator and emit a stable unusable-command finding
    (id suffix ``:empty-inapplicable-N``).
    """
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ws.read(path)
    # Replace the runnable command with an empty inapplicable marker so the
    # file no longer declares a runnable command and the empty inapplicable
    # marker should fail the empty-inapplicable check.
    lines = [line for line in content.splitlines() if not line.startswith(markers.COMMAND_MARKER)]
    lines.append("RALPH-INAPPLICABLE:")
    ws.write(path, "\n".join(lines) + "\n")
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    # Stable id suffix marks the offending empty-inapplicable line.
    assert any(
        i.startswith(f"{markers.ID_CMD_UNUSABLE}:testing-policy.md:empty-inapplicable-")
        for i in ids
    ), f"missing empty-inapplicable finding; observed: {ids}"
    # The file also lost its RALPH-COMMAND; the missing-command finding
    # must still fire so the user sees the dual defect.
    assert any(
        i == f"{markers.ID_CMD_UNUSABLE}:testing-policy.md:missing" for i in ids
    )


def test_empty_per_language_inapplicable_emits_finding() -> None:
    """AC-06: an empty ``RALPH-INAPPLICABLE:`` line inside a per-language
    block of typecheck/lint policies MUST be rejected with a stable
    ``:empty-inapplicable`` language-coverage finding, regardless of
    whether the same block also declares a real command.
    """
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    # Append an empty inapplicable declaration to the existing Python block
    # in typechecking-policy.md. The block already has a real command, so
    # only the empty-inapplicable gate is expected to fire (no
    # :empty-language finding, which is the *absence* gate).
    path = f"{markers.CANONICAL_DIR}typechecking-policy.md"
    content = ws.read(path)
    ws.write(path, content + "\nRALPH-INAPPLICABLE:\n")
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert any(
        i.startswith(
            f"{markers.ID_LANG_COVERAGE}:typechecking-policy.md:Python:empty-inapplicable"
        )
        for i in ids
    ), f"missing per-language empty-inapplicable finding; observed: {ids}"


def test_duplicate_complete_managed_block_emits_finding() -> None:
    """Regression: a fully compliant workspace whose AGENTS.md has TWO
    complete managed blocks appended MUST NOT validate as ready. The
    validator must surface a stable RWP-MARKER:agents-block:duplicate
    finding so the remediation agent reconciles the file.
    """
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    block = (
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        f"See {markers.CANONICAL_DIR}.\n"
        f"{markers.AGENTS_BLOCK_END}\n"
    )
    ws.write(markers.AGENTS_MD, block + block)
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert (
        f"{markers.ID_MARKER_MISSING}:agents-block:duplicate" in ids
    ), f"missing duplicate managed-block finding; observed: {ids}"
    # Project must NOT be ready.
    assert any(f.path == markers.AGENTS_MD for f in findings)


def test_unmatched_begin_only_emits_finding() -> None:
    """A pre-existing AGENTS.md with a begin marker but no end marker
    MUST emit a stable RWP-MARKER:agents-block:unmatched finding.
    """
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    ws.write(
        markers.AGENTS_MD,
        f"# Original\n\n{markers.AGENTS_BLOCK_BEGIN}\nincomplete\n",
    )
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert (
        f"{markers.ID_MARKER_MISSING}:agents-block:unmatched" in ids
    ), f"missing unmatched managed-block finding; observed: {ids}"


def test_unmatched_end_only_emits_finding() -> None:
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    ws.write(
        markers.AGENTS_MD,
        f"# Original\n\ntrailing\n{markers.AGENTS_BLOCK_END}\n",
    )
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert (
        f"{markers.ID_MARKER_MISSING}:agents-block:unmatched" in ids
    ), f"missing unmatched managed-block finding; observed: {ids}"


def test_misordered_markers_emit_finding() -> None:
    """An AGENTS.md whose end marker appears before its begin marker MUST
    emit a stable RWP-MARKER:agents-block:misordered finding.
    """
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    ws.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_END}\n{markers.AGENTS_BLOCK_BEGIN}\n"
        f"See {markers.CANONICAL_DIR}.\n",
    )
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert (
        f"{markers.ID_MARKER_MISSING}:agents-block:misordered" in ids
    ), f"missing misordered managed-block finding; observed: {ids}"


def test_managed_block_missing_both_markers_emits_finding() -> None:
    """An AGENTS.md without any managed block marker at all (but still
    referencing CANONICAL_DIR) MUST emit the RWP-MARKER:agents-block:missing
    finding so remediation installs a managed block.
    """
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    ws.write(
        markers.AGENTS_MD,
        f"# AGENTS.md\n\nSee {markers.CANONICAL_DIR} for policies.\n",
    )
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert (
        f"{markers.ID_MARKER_MISSING}:agents-block:missing" in ids
    ), f"missing missing-block finding; observed: {ids}"


def test_canonical_dir_outside_block_emits_finding() -> None:
    """Regression (analysis feedback #1): a fully compliant workspace whose
    AGENTS.md has CANONICAL_DIR referenced only OUTSIDE the managed block
    MUST emit the RWP-MARKER:canonical-dir-ref finding. The validator must
    NOT silently accept the reference when it does not appear in the block
    body. The remediation agent should add a reference INSIDE the block.
    """
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    # CANONICAL_DIR appears in user-authored prose BEFORE the managed block;
    # the block itself references nothing about the canonical dir.
    ws.write(
        markers.AGENTS_MD,
        f"# My Project\n\n"
        f"See {markers.CANONICAL_DIR} for policies.\n\n"
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        f"This block does not mention the canonical policy directory.\n"
        f"{markers.AGENTS_BLOCK_END}\n",
    )
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert (
        f"{markers.ID_MARKER_MISSING}:canonical-dir-ref" in ids
    ), (
        f"missing canonical-dir-ref finding when CANONICAL_DIR is only "
        f"outside the managed block; observed: {ids}"
    )
    # Project must NOT be ready.
    assert any(f.path == markers.AGENTS_MD for f in findings)


def test_canonical_dir_inside_block_emits_no_finding() -> None:
    """Counterpart: when the reference is INSIDE the managed block the gate
    is satisfied and no canonical-dir-ref finding fires (other than the
    well-formed managed-block finding, which is absent here).
    """
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    ws.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\n"
        f"All project policy lives under {markers.CANONICAL_DIR}.\n"
        f"{markers.AGENTS_BLOCK_END}\n",
    )
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert f"{markers.ID_MARKER_MISSING}:canonical-dir-ref" not in ids, (
        f"unexpected canonical-dir-ref finding when reference is inside "
        f"the block; observed: {ids}"
    )


def test_verification_bypass_empty_command_emits_finding() -> None:
    """Regression (analysis feedback #3): an empty ``RALPH-COMMAND:`` line
    under 'Bypass detection' MUST NOT be accepted as a valid bypass-audit
    gate. The validator must surface a stable
    ``RWP-CMD:verification-policy.md:bypass-cmd:empty`` finding so the
    remediation agent adds a real command.
    """
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    # Replace the only valid bypass command with an empty marker.
    path = f"{markers.CANONICAL_DIR}verification-policy.md"
    content = ws.read(path)
    lines = content.splitlines()
    new_lines: list[str] = []
    in_bypass = False
    for line in lines:
        if line.strip() == "## Bypass detection":
            in_bypass = True
            new_lines.append(line)
            continue
        # Drop existing RALPH-COMMAND lines inside the bypass block and
        # the following H2 boundary.
        if in_bypass and line.startswith("## "):
            # Append the empty command before the boundary heading.
            new_lines.append("RALPH-COMMAND:")
            in_bypass = False
            new_lines.append(line)
            continue
        if in_bypass and line.startswith(markers.COMMAND_MARKER):
            # Drop the real command; we'll add the empty marker below.
            continue
        new_lines.append(line)
    if in_bypass:
        new_lines.append("RALPH-COMMAND:")
    ws.write(path, "\n".join(new_lines) + "\n")
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert (
        f"{markers.ID_CMD_UNUSABLE}:verification-policy.md:bypass-cmd:empty"
        in ids
    ), (
        f"missing bypass-cmd:empty finding for an empty RALPH-COMMAND "
        f"under Bypass detection; observed: {ids}"
    )
    # Project must NOT be ready.
    assert any(f.path == path for f in findings)


def test_verification_bypass_placeholder_command_emits_finding() -> None:
    """Regression (analysis feedback #3): a placeholder RALPH-COMMAND
    under 'Bypass detection' MUST be rejected with the placeholder finding
    so the user sees actionable evidence for an unusable gate.
    """
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    path = f"{markers.CANONICAL_DIR}verification-policy.md"
    content = ws.read(path)
    lines = content.splitlines()
    new_lines: list[str] = []
    in_bypass = False
    for line in lines:
        if line.strip() == "## Bypass detection":
            in_bypass = True
            new_lines.append(line)
            continue
        if in_bypass and line.startswith("## "):
            new_lines.append("RALPH-COMMAND: TODO real-bypass-command")
            in_bypass = False
            new_lines.append(line)
            continue
        if in_bypass and line.startswith(markers.COMMAND_MARKER):
            continue
        new_lines.append(line)
    if in_bypass:
        new_lines.append("RALPH-COMMAND: TODO real-bypass-command")
    ws.write(path, "\n".join(new_lines) + "\n")
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert (
        f"{markers.ID_CMD_UNUSABLE}:verification-policy.md:bypass-cmd:placeholder"
        in ids
    ), (
        f"missing bypass-cmd:placeholder finding for a placeholder "
        f"RALPH-COMMAND under Bypass detection; observed: {ids}"
    )
    # Project must NOT be ready.
    assert any(f.path == path for f in findings)


def test_unapproved_command_at_normal_policy_scope_emits_finding() -> None:
    """Regression (analysis feedback): a non-empty, placeholder-free
    RALPH-COMMAND whose first whitespace-separated token is NOT on the
    :data:`markers.APPROVED_GATE_TOOLS` allowlist MUST be rejected by the
    validator at normal policy scope (i.e. ANY RALPH-COMMAND line in ANY
    policy file). The previously buggy check accepted arbitrary text such
    as ``definitely-not-a-command`` and produced ``[]`` findings for an
    ostensibly-ready project, violating the executor-gate contract. The
    validator must now emit a stable
    ``RWP-CMD:<filename-with-no-allowlist-cmd>:unapproved-cmd-N`` finding.
    """
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ws.read(path)
    # Replace the runnable ``echo ok`` command with a non-empty,
    # placeholder-free, but NOT-on-allowlist string. The ``definitely-not-a-command``
    # string is the exact reproducer from the analysis feedback.
    lines = content.splitlines()
    new_lines = [
        line if not line.startswith(markers.COMMAND_MARKER) else f"{markers.COMMAND_MARKER} definitely-not-a-command"
        for line in lines
    ]
    ws.write(path, "\n".join(new_lines) + "\n")
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert any(
        i == f"{markers.ID_CMD_UNUSABLE}:testing-policy.md:unapproved-cmd-1" for i in ids
    ), (
        f"missing unapproved-cmd-1 finding at normal policy scope for "
        f"'definitely-not-a-command'; observed: {sorted(ids)}"
    )
    # Project must NOT be ready.
    assert any(f.path == path for f in findings)


def test_verification_bypass_unapproved_command_emits_finding() -> None:
    """Regression (analysis feedback): a non-empty, placeholder-free
    RALPH-COMMAND whose first whitespace-separated token is NOT on the
    :data:`markers.APPROVED_GATE_TOOLS` allowlist MUST be rejected by the
    validator under 'Bypass detection' (verification-policy.md). The
    previously buggy code accepted any such text inside the bypass-detection
    block; the new logic mirrors the per-policy command gate and emits a
    stable ``RWP-CMD:verification-policy.md:bypass-cmd:unapproved`` finding.
    """
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    path = f"{markers.CANONICAL_DIR}verification-policy.md"
    content = ws.read(path)
    lines = content.splitlines()
    new_lines: list[str] = []
    in_bypass = False
    for line in lines:
        if line.strip() == "## Bypass detection":
            in_bypass = True
            new_lines.append(line)
            continue
        if in_bypass and line.startswith("## "):
            new_lines.append(f"{markers.COMMAND_MARKER} definitely-not-a-command")
            in_bypass = False
            new_lines.append(line)
            continue
        if in_bypass and line.startswith(markers.COMMAND_MARKER):
            continue
        new_lines.append(line)
    if in_bypass:
        new_lines.append(f"{markers.COMMAND_MARKER} definitely-not-a-command")
    ws.write(path, "\n".join(new_lines) + "\n")
    findings = validators.validate_readiness(ws, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert (
        f"{markers.ID_CMD_UNUSABLE}:verification-policy.md:bypass-cmd:unapproved"
        in ids
    ), (
        f"missing bypass-cmd:unapproved finding for a non-allowlist "
        f"RALPH-COMMAND under Bypass detection; observed: {sorted(ids)}"
    )
    # Project must NOT be ready.
    assert any(f.path == path for f in findings)

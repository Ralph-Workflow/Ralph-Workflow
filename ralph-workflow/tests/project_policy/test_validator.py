"""Tests for the deterministic project-policy validator."""

from __future__ import annotations

import functools

import pytest

from ralph.language_detector.models import ProjectStack
from ralph.project_policy import _content_checks, markers, starters, validators
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


@functools.cache
def _complete_policy_body(*, filename: str, lang: str | None = None) -> str:
    """Build a policy body that satisfies every structural check (no placeholder)."""
    lines = [
        markers.POLICY_SCHEMA_MARKER,
        f"{markers.POLICY_ID_PREFIX} {filename} -->",
        "",
        "# Title",
        "",
    ]
    facts = [
        line.replace("PROJECT-FACT-UNRESOLVED", "verified-value")
        for line in starters.read_starter(filename).splitlines()
        if line.startswith(markers.FACT_MARKER)
    ]
    # Iterate the required headings for this filename and emit each
    # section with the right shape (so the v5 guard-inventory check
    # passes and so every starter-specific heading remains satisfied).
    for heading in markers.REQUIRED_HEADINGS.get(filename, ()):
        lines.append(f"## {heading}")
        if heading == "Research basis":
            lines.extend(
                (
                    "",
                    "- publisher: Test Publisher",
                    "  title: Test Title",
                    "  http: https://example.com",
                    "  review date: 2026-07-11",
                    "",
                )
            )
        elif heading == "Bypass detection":
            lines.extend(("Real content.", "RALPH-COMMAND: make bypass-audit", ""))
        elif heading == markers.GUARD_INVENTORY_HEADING.removeprefix("## "):
            # v5: every applicable starter (core and conditional) carries
            # a Guard inventory table with the pinned header and at
            # least one data row whose Protected outcome names a real
            # detector observation (so the deterministic claim-scope
            # substring predicate passes). The sixth cell is the
            # ``Claim scope`` field required by B7.
            lines.extend(
                (
                    "",
                    "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Retirement trigger |",
                    "|---|---|---|---|---|---|",
                    f"| test-guard-{filename} | test owner | flags missing test evidence | missing RALPH-FACT line is recorded as a finding | when the test policy is removed | the count of missing RALPH-FACT lines per file |",
                    "",
                )
            )
        else:
            lines.extend(("Real content.", ""))
    lines.append("")
    lines.extend(facts)
    # Every complete policy must declare at least one resolved project
    # fact so the validator's no-fact gate cannot be silently satisfied by
    # the surrounding structural completeness.
    lines.append(f"RALPH-FACT: {filename}: path = docs/ralph-workflow-policy/{filename}")
    lines.append("RALPH-FACT: owner = test-owner")
    lines.append("")
    if lang is not None:
        lines.append(f"RALPH-LANG: {lang}")
    lines.append("RALPH-COMMAND: make test")
    return "\n".join(lines)


def test_frozen_older_schema_is_an_explicit_valid_choice() -> None:
    content = "\n".join(
        (
            "<!-- ralph-policy-schema: freeze v0 -->",
            "<!-- ralph-policy-id: testing-policy.md -->",
        )
    )
    findings = validators._check_markers(
        content,
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )
    assert findings == []


def test_malformed_schema_freeze_does_not_skip_upgrade() -> None:
    findings = validators._check_markers(
        "<!-- ralph-policy-schema: freeze version-old -->\n"
        "<!-- ralph-policy-id: testing-policy.md -->",
        "docs/ralph-workflow-policy/testing-policy.md",
        "testing-policy.md",
    )
    assert any("schema-testing-policy.md" in finding.requirement_id for finding in findings)


@pytest.mark.parametrize(
    ("policy_filename", "unsupported_reason", "tool_noun", "warning_text"),
    [
        (
            "typechecking-policy.md",
            "legacy errors; review trigger: later",
            "checker",
            "type checking unavailable",
        ),
        (
            "linting-policy.md",
            "not configured; review trigger: later",
            "linter",
            "linting unavailable",
        ),
    ],
)
def test_tool_exception_requires_enumerated_case_and_warning_fields(
    policy_filename: str, unsupported_reason: str, tool_noun: str, warning_text: str
) -> None:
    generic = _content_checks._check_individual_inapplicables(
        [unsupported_reason],
        f"docs/ralph-workflow-policy/{policy_filename}",
        policy_filename,
    )
    assert generic

    exceptional = _content_checks._check_individual_inapplicables(
        [
            f"exceptional case: no suitable maintained {tool_noun} exists; "
            f"evidence: docs/tool-review.md; owner: maintainer; expiry: 2027-01-01; "
            f"warning: {warning_text}; review trigger: language release"
        ],
        f"docs/ralph-workflow-policy/{policy_filename}",
        policy_filename,
    )
    assert exceptional == []


def test_applicability_override_rejects_whitespace_reason_and_trigger() -> None:
    ws = MemoryWorkspace()
    ws.write(
        markers.APPLICABILITY_OVERRIDES_PATH,
        '[domains]\nprivacy = "inactive; reason:   ; review trigger:   "\n',
    )
    findings = validators._check_applicability_overrides(ws)
    assert any("override-empty" in finding.requirement_id for finding in findings)


@functools.lru_cache(maxsize=1)
def _seeded_core_files() -> tuple[tuple[str, str], ...]:
    files: list[tuple[str, str]] = [
        (markers.PORTFOLIO_PATH, starters.read_starter("policy-portfolio.toml"))
    ]
    for filename in markers.CORE_POLICY_FILES:
        files.append(
            (
                f"{markers.CANONICAL_DIR}{filename}",
                _complete_policy_body(
                    filename=filename,
                    lang=None
                    if filename not in {"typechecking-policy.md", "linting-policy.md"}
                    else "Python",
                ),
            )
        )
    return tuple(files)


def _seed_all_core_complete(workspace: MemoryWorkspace, stack: ProjectStack) -> None:
    """Seed every core + required-conditional file as fully complete."""
    workspace.mkdirs(markers.CANONICAL_DIR.rstrip("/"))
    for path, content in _seeded_core_files():
        workspace.write(path, content)


def _seed_agents_md(workspace: MemoryWorkspace) -> None:
    workspace.write(
        markers.AGENTS_MD,
        f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
    )


def _seed_claude_md(workspace: MemoryWorkspace) -> None:
    workspace.write(markers.CLAUDE_MD, "# CLAUDE.md\n\nSee AGENTS.md for project policy.\n")


@functools.lru_cache(maxsize=1)
def _ready_workspace_state() -> tuple[dict[str, str], set[str]]:
    ws = MemoryWorkspace()
    _seed_agents_md(ws)
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    return dict(ws._storage), set(ws._dirs)


@pytest.fixture
def ready_workspace() -> MemoryWorkspace:
    storage, dirs = _ready_workspace_state()
    ws = MemoryWorkspace()
    ws._storage = dict(storage)
    ws._dirs = set(dirs)
    return ws


def test_no_findings_on_fully_populated_project(ready_workspace: MemoryWorkspace) -> None:
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    assert findings == []


def test_agents_md_missing_emits_finding() -> None:
    ws = MemoryWorkspace()
    _seed_claude_md(ws)
    _seed_all_core_complete(ws, _stack_with())
    findings = validators.validate_readiness(ws, _stack_with())
    paths = [f.path for f in findings]
    assert markers.AGENTS_MD in paths


def test_missing_core_file_emits_finding(ready_workspace: MemoryWorkspace) -> None:
    ready_workspace.remove(f"{markers.CANONICAL_DIR}testing-policy.md")
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    paths = [f.path for f in findings]
    assert f"{markers.CANONICAL_DIR}testing-policy.md" in paths


def test_heading_missing_emits_finding(ready_workspace: MemoryWorkspace) -> None:
    # Remove one heading
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ready_workspace.read(path)
    content = content.replace("## Purpose and scope", "## Different heading")
    ready_workspace.write(path, content)
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    paths = [f.path for f in findings]
    assert path in paths


def test_placeholder_token_emits_finding(ready_workspace: MemoryWorkspace) -> None:
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ready_workspace.read(path)
    content = content.replace("Real content.", "TODO real content")
    ready_workspace.write(path, content)
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    assert any(f.path == path and "placeholder" in f.missing_evidence.lower() for f in findings)


def test_missing_command_and_inapplicable_emits_finding(ready_workspace: MemoryWorkspace) -> None:
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ready_workspace.read(path)
    # Strip the RALPH-COMMAND line
    lines = content.splitlines()
    lines = [line for line in lines if not line.startswith(markers.COMMAND_MARKER)]
    ready_workspace.write(path, "\n".join(lines) + "\n")
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    assert any(
        f.path == path and f.requirement_id.startswith(markers.ID_CMD_UNUSABLE) for f in findings
    )


def test_no_completion_marker_is_required_for_readiness(ready_workspace: MemoryWorkspace) -> None:
    """Completion is the absence of unresolved markers, not a certification
    comment: a fully-resolved project reaches READY without any completion
    marker, and the validator never emits a completion finding."""
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    assert findings == []
    for filename in markers.CORE_POLICY_FILES:
        assert "ralph-policy-complete" not in ready_workspace.read(
            f"{markers.CANONICAL_DIR}{filename}"
        )


def test_per_language_coverage_required_for_secondary_language(
    ready_workspace: MemoryWorkspace,
) -> None:
    # Stack has Python + TypeScript but neither policy declares TypeScript.
    stack = _stack_with(primary="Python", secondary=["TypeScript"])
    findings = validators.validate_readiness(ready_workspace, stack)
    ids = {f.requirement_id for f in findings}
    assert any(
        i.startswith(f"{markers.ID_LANG_COVERAGE}:typechecking-policy.md:TypeScript") for i in ids
    )
    assert any(
        i.startswith(f"{markers.ID_LANG_COVERAGE}:linting-policy.md:TypeScript") for i in ids
    )


def test_bypass_detection_section_required_for_verification_policy(
    ready_workspace: MemoryWorkspace,
) -> None:
    path = f"{markers.CANONICAL_DIR}verification-policy.md"
    content = ready_workspace.read(path).replace("## Bypass detection", "## Different")
    ready_workspace.write(path, content)
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    assert any(
        f.path == path and "bypass detection" in f.missing_evidence.lower() for f in findings
    )


@pytest.mark.parametrize(
    ("marker_content", "emits_finding"),
    [
        ("Some content with a recognized heading.\n", True),
        (markers.MIGRATED_MARKER_TEMPLATE.format(target="testing-policy.md") + "\n", False),
    ],
)
def test_migration_candidate_resolution(
    ready_workspace: MemoryWorkspace, marker_content: str, emits_finding: bool
) -> None:
    ready_workspace.write("docs/testing.md", f"# Testing Policy\n\n{marker_content}")
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    has_finding = any(f.path == "docs/testing.md" for f in findings)
    assert has_finding == emits_finding


def test_citation_field_missing_emits_finding(ready_workspace: MemoryWorkspace) -> None:
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ready_workspace.read(path)
    # Replace the citation block to be missing the URL field.
    content = content.replace("http: https://example.com", "url: missing")
    ready_workspace.write(path, content)
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    assert any(
        f.path == path and f.requirement_id.startswith(markers.ID_CITATION_MISSING)
        for f in findings
    )


def _insert_citation_into_research_basis(ws: MemoryWorkspace, path: str, addition: str) -> None:
    """Insert a citation block inside the ``## Research basis`` section.

    The validator's :func:`_citation_blocks` helper slices the file
    between ``## Research basis`` and the next H2 heading. Appending
    the new block at the END of the file places it past the next
    H2 and the validator never sees it; the helpers below insert
    the block immediately after the existing first citation block,
    still inside the Research basis section.
    """
    content = ws.read(path)
    lines = content.splitlines()
    start_idx: int | None = None
    for idx, line in enumerate(lines):
        if line.strip().lower() == "## research basis":
            start_idx = idx + 1
            break
    if start_idx is None:
        # No Research basis section: append one at the end.
        ws.write(path, content.rstrip("\n") + "\n\n## Research basis\n\n" + addition.lstrip())
        return
    # Find the end of the first citation block: walk forward until a
    # blank line followed by either another citation (starts with
    # ``-``) or the next H2. Insert just before the H2 boundary.
    end_idx = len(lines)
    for idx in range(start_idx, len(lines)):
        line = lines[idx]
        if line.startswith("## ") and idx > start_idx:
            end_idx = idx
            break
    insertion = ("\n" + addition.strip("\n") + "\n\n").lstrip("\n")
    new_lines = [*lines[:end_idx], insertion, *lines[end_idx:]]
    ws.write(path, "\n".join(new_lines) + "\n")


@pytest.mark.parametrize(
    ("citation_entry", "pre_existing_file", "expected_finding_kind"),
    [
        (
            "- publisher: In-tree test fixture\n  title: explicit path acceptance\n  http: https://example.com\n  review date: 2026-01-01\n  path: tests/test_validator.py\n",
            ("tests/test_validator.py", "# real file"),
            None,
        ),
        (
            "- publisher: Imaginary\n  title: explicit path failure\n  http: https://example.com\n  review date: 2026-01-01\n  path: tests/__nonexistent_file__.py\n",
            None,
            "path-missing",
        ),
        (
            "- publisher: prose value, no explicit `path:` key\n  title: https-only citation\n  http: https://example.com\n  review date: 2026-01-01\n",
            None,
            None,
        ),
        (
            "- publisher: bad path\n  title: malformed path\n  http: https://example.com\n  review date: 2026-01-01\n  path: https://example.com/foo.py\n",
            None,
            "path-malformed",
        ),
    ],
)
def test_citation_explicit_path_variants(
    ready_workspace: MemoryWorkspace,
    citation_entry: str,
    pre_existing_file: tuple[str, str] | None,
    expected_finding_kind: str | None,
) -> None:
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    if pre_existing_file is not None:
        ready_workspace.write(pre_existing_file[0], pre_existing_file[1])
    _insert_citation_into_research_basis(ready_workspace, path, citation_entry)
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    if expected_finding_kind is None:
        path_findings = [f for f in findings if f.path == path and "path-" in f.requirement_id]
        assert path_findings == [], f"unexpected path findings: {path_findings}"
    else:
        matching = [
            f
            for f in findings
            if f.path == path
            and f.requirement_id.startswith(markers.ID_CITATION_MISSING)
            and expected_finding_kind in f.requirement_id
        ]
        assert matching, f"missing expected {expected_finding_kind} finding in {findings}"


def test_design_system_required_emits_design_system_finding(
    ready_workspace: MemoryWorkspace,
) -> None:
    # Stack has a UI framework but no design-system policy file.
    stack = _stack_with(secondary=["CSS"])
    findings = validators.validate_readiness(ready_workspace, stack)
    assert any("design-system-policy.md" in f.path for f in findings)


def test_starter_content_fails_validation_until_customized() -> None:
    """A freshly-seeded starter has a banner and placeholders -> failing."""
    ws = MemoryWorkspace()
    starters.seed_starter_into(ws, "testing-policy.md")
    findings = validators.validate_readiness(ws, _stack_with())
    paths = [f.path for f in findings]
    assert f"{markers.CANONICAL_DIR}testing-policy.md" in paths


def test_removed_fact_lines_emits_no_fact_finding_per_policy(
    ready_workspace: MemoryWorkspace,
) -> None:
    """AC-05/AC-08: removing every RALPH-FACT line from a complete policy MUST
    surface a stable :data:`RWP-PLACEHOLDER:<filename>:no-fact` finding and
    the project MUST NOT reach READY. This is the regression for the
    analyzer-found defect where a fully-structured file with no
    machine-checkable project facts still validated as complete.
    """
    # Strip every RALPH-FACT line from every core policy.
    stripped_paths: list[str] = []
    for filename in markers.CORE_POLICY_FILES:
        path = f"{markers.CANONICAL_DIR}{filename}"
        content = ready_workspace.read(path)
        lines = [line for line in content.splitlines() if not line.startswith(markers.FACT_MARKER)]
        ready_workspace.write(path, "\n".join(lines) + "\n")
        stripped_paths.append(path)
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    ids_by_path = {(f.path, f.requirement_id) for f in findings}
    for path in stripped_paths:
        # Stable id suffix ``:no-fact`` so the user can grep for it; the
        # analyzer's repro produced exactly this missing finding for each
        # affected policy.
        assert (path, f"{markers.ID_PLACEHOLDER}:{path.split('/')[-1]}:no-fact") in ids_by_path, (
            f"missing RWP-PLACEHOLDER:no-fact finding for {path}; "
            f"observed ids: {[i for p, i in ids_by_path if p == path]}"
        )


def test_empty_general_inapplicable_emits_finding(ready_workspace: MemoryWorkspace) -> None:
    """AC-06: an empty ``RALPH-INAPPLICABLE:`` line at file scope MUST be
    rejected by the validator and emit a stable unusable-command finding
    (id suffix ``:empty-inapplicable-N``).
    """
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ready_workspace.read(path)
    # Replace the runnable command with an empty inapplicable marker so the
    # file no longer declares a runnable command and the empty inapplicable
    # marker should fail the empty-inapplicable check.
    lines = [line for line in content.splitlines() if not line.startswith(markers.COMMAND_MARKER)]
    lines.append("RALPH-INAPPLICABLE:")
    ready_workspace.write(path, "\n".join(lines) + "\n")
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    ids = {f.requirement_id for f in findings}
    # Stable id suffix marks the offending empty-inapplicable line.
    assert any(
        i.startswith(f"{markers.ID_CMD_UNUSABLE}:testing-policy.md:empty-inapplicable-")
        for i in ids
    ), f"missing empty-inapplicable finding; observed: {ids}"
    # The file also lost its RALPH-COMMAND; the missing-command finding
    # must still fire so the user sees the dual defect.
    assert any(i == f"{markers.ID_CMD_UNUSABLE}:testing-policy.md:missing" for i in ids)


def test_empty_per_language_inapplicable_emits_finding(ready_workspace: MemoryWorkspace) -> None:
    """AC-06: an empty ``RALPH-INAPPLICABLE:`` line inside a per-language
    block of typecheck/lint policies MUST be rejected with a stable
    ``:empty-inapplicable`` language-coverage finding, regardless of
    whether the same block also declares a real command.
    """
    # Append an empty inapplicable declaration to the existing Python block
    # in typechecking-policy.md. The block already has a real command, so
    # only the empty-inapplicable gate is expected to fire (no
    # :empty-language finding, which is the *absence* gate).
    path = f"{markers.CANONICAL_DIR}typechecking-policy.md"
    content = ready_workspace.read(path)
    ready_workspace.write(path, content + "\nRALPH-INAPPLICABLE:\n")
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert any(
        i.startswith(f"{markers.ID_LANG_COVERAGE}:typechecking-policy.md:Python:empty-inapplicable")
        for i in ids
    ), f"missing per-language empty-inapplicable finding; observed: {ids}"


@pytest.mark.parametrize(
    ("agents_md_content", "expected_suffix"),
    [
        (
            (
                f"{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n"
            )
            * 2,
            "duplicate",
        ),
        (
            f"# Original\n\n{markers.AGENTS_BLOCK_BEGIN}\nincomplete\n",
            "unmatched",
        ),
        (
            f"# Original\n\ntrailing\n{markers.AGENTS_BLOCK_END}\n",
            "unmatched",
        ),
        (
            f"{markers.AGENTS_BLOCK_END}\n{markers.AGENTS_BLOCK_BEGIN}\nSee {markers.CANONICAL_DIR}.\n",
            "misordered",
        ),
        (
            f"# AGENTS.md\n\nSee {markers.CANONICAL_DIR} for policies.\n",
            "missing",
        ),
    ],
)
def test_managed_block_marker_variants_emit_finding(
    ready_workspace: MemoryWorkspace, agents_md_content: str, expected_suffix: str
) -> None:
    """AGENTS.md marker errors (duplicate, unmatched, misordered, missing) emit stable findings."""
    ready_workspace.write(markers.AGENTS_MD, agents_md_content)
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert f"{markers.ID_MARKER_MISSING}:agents-block:{expected_suffix}" in ids
    assert any(f.path == markers.AGENTS_MD for f in findings)


@pytest.mark.parametrize(
    ("agents_md_body", "should_have_finding"),
    [
        (
            f"# My Project\n\nSee {markers.CANONICAL_DIR} for policies.\n\n"
            f"{markers.AGENTS_BLOCK_BEGIN}\nThis block does not mention the canonical policy directory.\n{markers.AGENTS_BLOCK_END}\n",
            True,
        ),
        (
            f"{markers.AGENTS_BLOCK_BEGIN}\nAll project policy lives under {markers.CANONICAL_DIR}.\n{markers.AGENTS_BLOCK_END}\n",
            False,
        ),
    ],
)
def test_canonical_dir_reference_location(
    ready_workspace: MemoryWorkspace, agents_md_body: str, should_have_finding: bool
) -> None:
    ready_workspace.write(markers.AGENTS_MD, agents_md_body)
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    ids = {f.requirement_id for f in findings}
    has_finding = f"{markers.ID_MARKER_MISSING}:canonical-dir-ref" in ids
    assert has_finding == should_have_finding
    if should_have_finding:
        assert any(f.path == markers.AGENTS_MD for f in findings)


@pytest.mark.parametrize(
    ("replacement_command", "expected_suffix"),
    [
        ("RALPH-COMMAND:", "empty"),
        ("RALPH-COMMAND: TODO real-bypass-command", "placeholder"),
        (f"{markers.COMMAND_MARKER} definitely-not-a-command", "unapproved"),
    ],
)
def test_verification_bypass_command_variants_emit_finding(
    ready_workspace: MemoryWorkspace, replacement_command: str, expected_suffix: str
) -> None:
    """An invalid RALPH-COMMAND under 'Bypass detection' must emit the appropriate bypass-cmd finding."""
    path = f"{markers.CANONICAL_DIR}verification-policy.md"
    content = ready_workspace.read(path)
    lines = content.splitlines()
    new_lines: list[str] = []
    in_bypass = False
    for line in lines:
        if line.strip() == "## Bypass detection":
            in_bypass = True
            new_lines.append(line)
            continue
        if in_bypass and line.startswith("## "):
            new_lines.append(replacement_command)
            in_bypass = False
            new_lines.append(line)
            continue
        if in_bypass and line.startswith(markers.COMMAND_MARKER):
            continue
        new_lines.append(line)
    if in_bypass:
        new_lines.append(replacement_command)
    ready_workspace.write(path, "\n".join(new_lines) + "\n")
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert f"{markers.ID_CMD_UNUSABLE}:verification-policy.md:bypass-cmd:{expected_suffix}" in ids
    assert any(f.path == path for f in findings)


def test_unapproved_command_at_normal_policy_scope_emits_finding(
    ready_workspace: MemoryWorkspace,
) -> None:
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
    path = f"{markers.CANONICAL_DIR}testing-policy.md"
    content = ready_workspace.read(path)
    lines = content.splitlines()
    new_lines = [
        line
        if not line.startswith(markers.COMMAND_MARKER)
        else f"{markers.COMMAND_MARKER} definitely-not-a-command"
        for line in lines
    ]
    ready_workspace.write(path, "\n".join(new_lines) + "\n")
    findings = validators.validate_readiness(ready_workspace, _stack_with())
    ids = {f.requirement_id for f in findings}
    assert any(i == f"{markers.ID_CMD_UNUSABLE}:testing-policy.md:unapproved-cmd-1" for i in ids), (
        f"missing unapproved-cmd-1 finding at normal policy scope for "
        f"'definitely-not-a-command'; observed: {sorted(ids)}"
    )
    # Project must NOT be ready.
    assert any(f.path == path for f in findings)


def test_portfolio_manifest_is_required_by_public_validator() -> None:
    ws = MemoryWorkspace()

    findings = validators.validate_readiness(ws, _stack_with())

    finding = next(item for item in findings if item.requirement_id == "RWP-PORTFOLIO:missing")
    assert finding.path == markers.PORTFOLIO_PATH
    assert "bounded verification portfolio" in finding.required_outcome


def test_invalid_portfolio_blocks_readiness_with_protected_outcome() -> None:
    ws = MemoryWorkspace()
    ws.write(markers.PORTFOLIO_PATH, 'schema_version = "invalid"')

    findings = validators.validate_readiness(ws, _stack_with())

    finding = next(item for item in findings if item.requirement_id == "RWP-PORTFOLIO:invalid")
    assert finding.path == markers.PORTFOLIO_PATH
    assert "protected outcomes" in finding.required_outcome
    assert "schema_version" in finding.missing_evidence


_GI_H = (
    "| Guard | Owner | Protected outcome | Detected condition | Claim scope | Retirement trigger |"
)
_GI_S = "|---|---|---|---|---|---|"
_GI_R = "| g | o | p | d | r | s |"


@pytest.mark.parametrize(
    ("table_body", "expected_suffix"),
    [
        (f"{_GI_H}\n{_GI_S}\n| g | o | p | d |", "malformed"),
        (f"{_GI_H}\n{_GI_S}\n| g | o | p | d | r | s | extra |", "malformed"),
        (f"{_GI_H}\n{_GI_R}", "malformed"),
        (f"{_GI_H}\n{_GI_S}\n| g |  | p | d | r | s |", "row-1:empty-owner"),
        (f"{_GI_H}\n{_GI_S}\n| g | o | p | d |  | s |", "CLAIM-SCOPE-MISSING"),
    ],
)
def test_guard_inventory_pipe_grammar_and_empty_fields(
    table_body: str, expected_suffix: str
) -> None:
    content = f"## Guard inventory\n\n{table_body}\n"
    findings = _content_checks._check_guard_inventory(
        content, "docs/ralph-workflow-policy/testing-policy.md", "testing-policy.md"
    )
    # The empty-Claim-scope cell maps to the stable v5 contract id
    # ``RWP-INVENTORY:CLAIM-SCOPE-MISSING`` (no filename/row
    # suffix); every other finding keeps the per-row
    # ``RWP-INVENTORY:<file>:<suffix>`` shape.
    if expected_suffix == "CLAIM-SCOPE-MISSING":
        expected_id = markers.CLAIM_SCOPE_MISSING_FINDING_ID
    else:
        expected_id = f"{markers.ID_INVENTORY}:testing-policy.md:{expected_suffix}"
    assert any(f.requirement_id == expected_id for f in findings)

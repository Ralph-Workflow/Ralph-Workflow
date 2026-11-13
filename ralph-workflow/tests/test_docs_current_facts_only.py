from __future__ import annotations

from pathlib import Path

import pytest

from tests.doc_roots import REPOSITORY_ROOT

_FORBIDDEN_TEXT_BY_DOCUMENT: tuple[tuple[str, tuple[str, ...]], ...] = (
    (
        "docs/agents/verification.md",
        (
            "Documentation Review Note",
            "What changed.",
        ),
    ),
    (
        "ralph-workflow/docs/agents/verification.md",
        ("used to hold an older variant",),
    ),
    (
        "ralph-workflow/CONTRIBUTING.md",
        ("missing docstring",),
    ),
    (
        "docs/code-style/documentation-rubric.md",
        ("Required review note",),
    ),
    (
        "docs/ralph-workflow-policy/verification-policy.md",
        ("exists because its rules previously lived only in",),
    ),
    (
        "docs/ralph-workflow-policy/policy-portfolio.toml",
        (
            "documented retirement decisions (wt-026 docs/dedup)",
            "names the canonical Sphinx routes as the surviving evidence location",
            "was removed in wt-01",
            "del width  # reserved for future width-aware wrapping",
            "the orphan constant tests/doc_roots.py:REPO_ROOT_DOCS_PERFORMANCE_DIR",
            "note dated 2026-",
            "review note dated 2026-10-03",
            "Review note dated 2026-10-03",
            "Review note dated 2026-10-04",
            "docs-owner review note",
            "docs-owner review note dated 2026-10-03 against f05524607",
            "docs-owner review note dated 2026-10-03 against fb0abd772",
            "the earlier 'watches:' prefix counted zero",
            "that is a category-2 (editorial) judgment, not an objective behaviour contract",
            "that is a category-2 editorial judgement, not an objective behaviour contract",
            "that is a category-2 (editorial) judgement about prose wording,"
            " not a falsifiable behaviour contract",
            "that is a category-3 implementation-coupled assertion (text-shape)",
            "that is a category-3 implementation-coupled assertion"
            " (private storage + literal SQL text)",
            "which is a category-4 third-party liveness check, not a behaviour contract",
            "would have broken it without changing observable behaviour",
            "would have broken the oracles without changing observable behaviour",
            "would have broken the oracle without changing observable behaviour",
            "No replacement is possible because the production edit-preview surface"
            " has no row-width floor",
            "A vendor outage would have flipped the gate red for a regression we cannot fix",
            "The test could never fail (category-1 vacuous).",
            "The removed tests always skipped because their retired guide paths"
            " are absent from a clean clone",
            "Every test in the removed file skipped because the archived performance"
            " directory is absent from a clean clone",
            "All parameter cases skipped because every retired code-style leaf path"
            " is absent from the clean clone",
            "The removed parametrize case always skipped",
            "The removed test_agy_mcp_setup_reflects_pty_injection",
            "The removed test_index_states_python_status",
            "The removed test_watchdog_evaluate_call_sites_unchanged",
            "The removed test_doc_has_no_forbidden_worktree_phrases",
            "The removed test_observe_line_is_no_op",
            "The removed test_upsert_manifest_many_writes_and_updates",
            "Each removed test_live_search_returns_results*",
            "The removed test_every_claimed_test_fails_under_its_regression_probe",
            "The removed tests in tests/test_subagent_visibility_architecture.py",
            "The removed test_visual_floor_over_width_unicode_row_fixture_is_rejected",
            "the removed test re-ran the public audit_resource_lifecycle_directory API",
            "redundant public-audit duplicate removed",
            "lines 585-588",
            "the textual call-site count no longer carries independent coverage",
            "TestWarmRefreshBatchingExercisesBulkApis",
            "test_a_*_has_marker",
            "docs/sphinx/architecture/parallel-execution.md",
            "docs/sphinx/architecture/process-pool.md",
            "docs/sphinx/architecture/mcp-upstream-proxy.md",
            "test_parallel_docs_no_worktree_language.py",
            "would re-enable cross-pipeline parallel switches that the design forbids",
            "that assertion is independent of observe_line",
            "supports_session_continuation is a fixed class-level property",
            "left the oracle true",
            "carried an unconditional @pytest.mark.skip",
            "the body was a no-op stub that did not even iterate _CLAIMS usefully",
            "the oracle was defined and consumed entirely inside the test",
            "so the test pinned no behaviour",
            "used inspect.getsource",
            "read store._conn directly",
            "asserted on the literal manifest SQL columns",
            "exercised a vendor's uptime using our gate",
            "when the env credential was absent the test skipped",
            "it asserted only 'vendor returned non-empty results'",
            "remove absent-path guards",
            "remove absent-archive guards",
            "remove absent-leaf guards",
            "remove prose-wording assertions",
            "remove text-shape assertion",
            "remove the vacuous oracle",
            "remove vendor-liveness probes",
            "remove unconditional-skip probe-injection stub",
            "remove source-text shape oracles",
            "remove vacuous test-local oracle",
            "remove the redundant public-audit duplicate",
            "remove absent-path parametrize case",
            "remove from the default suite",
            "re-add the AGY case when the file is reintroduced",
            "companion coverage in test_generic_child_signal.py pins the classifier itself",
            "production code documentation",
            "test exceeded 1.0 seconds, observed 2026-10-04",
            "tests.conftest.TestExecutionTimeoutError",
        ),
    ),
    (
        "docs/ralph-workflow-policy/linting-policy.md",
        (
            "shell-script `scripts/verify_social_proof.py`",
            "per its module docstring",
            "documented consumer",
            "removal trigger",
        ),
    ),
    (
        "AGENTS.md",
        ("exists because its rules previously lived only in",),
    ),
    (
        "docs/agents/verification.md",
        (
            "exists precisely because its rules previously lived only in",
            "were removed in the 2026-07-07 docs cleanup",
            "The previously serial",
        ),
    ),
    (
        "docs/ralph-workflow-policy/verification-policy.md",
        (
            "It is wired into `_VERIFY_STEPS` precisely because",
            "rotted undetected",
            "formerly three separate steps merged",
        ),
    ),
    (
        "docs/ralph-workflow-policy/gate-script-policy.md",
        (
            "is no longer present after wt-038",
            "closed the strict-mode gap",
        ),
    ),
    (
        "docs/agents/architecture.md",
        ("used to be duplicated across every parser's",),
    ),
    (
        "ralph-workflow/docs/agents/architecture.md",
        ("Documentation review note",),
    ),
    (
        "ralph-workflow/docs/agents/filesystem-activity-traceability.md",
        ("Documentation review note",),
    ),
    (
        "ralph-workflow/docs/architecture/adr-0009-pipeline-source-of-truth-ownership.md",
        ("Documentation review note",),
    ),
    (
        "ralph-workflow/docs/sphinx/operator-states.md",
        ("Documentation review note",),
    ),
    (
        "ralph-workflow/docs/sphinx/recovery.md",
        ("Documentation review note",),
    ),
    (
        "docs/code-style/index.md",
        (
            "public module docstrings",
            "Keep public module and package docstrings",
        ),
    ),
    (
        "docs/agents/workspace-trait.md",
        ("the module docstring in",),
    ),
    (
        "docs/ralph-workflow-policy/testing-policy.md",
        ("in the test docstring",),
    ),
    (
        "ralph-workflow/ralph/mcp/artifacts/format_docs/examples/issues.md",
        (
            "lacks a comment explaining",
            "Add a one-line comment",
        ),
    ),
    (
        "ralph-workflow/docs/architecture/adr-0001-interrupt-architecture.md",
        ("method's docstring notes",),
    ),
    (
        "ralph-workflow/docs/sphinx/mcp-restart-decisions.md",
        ("its own docstring defers",),
    ),
    (
        "ralph-workflow/docs/architecture/adr-0006-owned-landing-recovery.md",
        (
            "previously reached the invariant directly",
            "deleted an unfinished landing receipt",
        ),
    ),
    (
        "ralph-workflow/docs/architecture/adr-0007-integration-source-of-truth.md",
        (
            "Previously, lifecycle truth was derived",
            "immediate production reproduction",
            "core decisions",
            "false progress (premature dispatch)",
        ),
    ),
    (
        "ralph-workflow/docs/architecture/adr-0009-pipeline-source-of-truth-ownership.md",
        (
            "replaced the live raw writers",
            "used to branch on the prose prefix",
            "used to derive the",
            "used to authorize",
        ),
    ),
    (
        "ralph-workflow/docs/agents/watchdog-spec.md",
        (
            "used to be two independent arbiters that drifted apart",
            "This split produced visible",
        ),
    ),
    (
        "ralph-workflow/docs/sphinx/agent-compatibility.md",
        (
            "previously raised `MissingCredentialsError",
            "A live run against `ccs/glm` confirmed",
        ),
    ),
    (
        "ralph-workflow/docs/architecture/adr-0008-integration-lifecycle-consolidation.md",
        (
            "explicitly deferred several areas in",
            "This ADR supersedes the",
        ),
    ),
    (
        "ralph-workflow/docs/architecture/adr-0010-post-fix-source-of-truth.md",
        ("**Before.**",),
    ),
    (
        "ralph-workflow/docs/sphinx/operator-states.md",
        (
            "left after the audit",
            "The migration covered",
        ),
    ),
    (
        "ralph-workflow/docs/agents/exec-long-running-root-cause.md",
        (
            "Historical (pre-§3b) wording",
            "no longer holds",
            "no longer cites a local-source URL",
            "## Cause 1 (CONFIRMED) — `inactivity` is output-only (HISTORICAL)",
            "the structured error template `hard_cap=False` / `inactivity=True`",
            'prompts the agent to "raise `timeout_ms` up to the cap"',
            "which the production entry path does not take.",
        ),
    ),
    (
        "ralph-workflow/docs/sphinx/advanced-pipeline-configuration.md",
        (
            "### Policy v2 migration note (historical)",
            "The historical migration from a top-level",
            "(introduced in the policy v2 overhaul)",
            "moved `max_parallel_workers`, `max_work_units`, "
            "`require_allowed_directories`, and `post_fanout_verification` "
            "under the development phase",
        ),
    ),
    (
        "ralph-workflow/docs/sphinx/troubleshooting.md",
        (
            "was historically returned as a tool result",
            "A relay sender failure now also fails MCP health probes",
            "Silence still ends the session as `CONFLICT_INACTIVITY`",
        ),
    ),
    (
        "ralph-workflow/docs/sphinx/concepts.md",
        (
            "### Tradeoffs the simple core accepts",
            "### Why policy is the right boundary",
            "Two alternatives were possible:",
            "Hard-code the pipeline in Python",
            "Make the user write a config from scratch",
            "Ralph Workflow picks a third shape",
            "### The tradeoffs",
            "The composed shape accepts three costs",
            "The tradeoff buys verification, recovery, and reviewability",
            "The tradeoff buys not having to babysit",
            "two-file minimum",
        ),
    ),
)


def _read(path: Path) -> str:
    assert path.is_file(), f"expected shipped document at {path}"
    return path.read_text(encoding="utf-8")


@pytest.mark.parametrize(
    ("relative_path", "forbidden_texts"),
    _FORBIDDEN_TEXT_BY_DOCUMENT,
    ids=[relative_path for relative_path, _ in _FORBIDDEN_TEXT_BY_DOCUMENT],
)
def test_documentation_states_current_facts_only(
    relative_path: str,
    forbidden_texts: tuple[str, ...],
) -> None:
    text = _read(REPOSITORY_ROOT / relative_path)
    still_present = [forbidden_text for forbidden_text in forbidden_texts if forbidden_text in text]
    assert not still_present, f"{relative_path} still carries narration text: {still_present}"

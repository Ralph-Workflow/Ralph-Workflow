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
        "docs/superpowers/specs/2026-07-14-telemetry-identity-lifecycle-design.md",
        (
            "Documentation review note:",
            "identity-file comment",
            "comment to name the canonical location",
        ),
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
        ),
    ),
    (
        "docs/ralph-workflow-policy/linting-policy.md",
        (
            "shell-script `scripts/verify_social_proof.py`",
            "per its module docstring",
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
        "ralph-workflow/docs/plans/2026-07-03-fs-churn-reduction.md",
        (
            "completion_receipts.py` docstring",
            "keep module docstring",
        ),
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

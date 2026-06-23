"""Regression tests locking the ``## Canonical validator errors to fix`` section.

The plan.md format documentation is the source of truth that the MCP
planning helpers wrap with the ``Fix this by reading`` envelope. The
``## Canonical validator errors to fix`` H2 must exist between
``## Dumb-proof checklist`` and ``## Module family`` so a cheap-model
agent that lands on the format doc after a rejection can find the
matching literal cross-section validator error string and a one-line
fix without re-reading the validator source.

The tests run in-process (no real I/O, no subprocess, no
``time.sleep``) and complete in well under one second.
"""

from __future__ import annotations

from pathlib import Path

import pytest


def _load_plan_md() -> str:
    """Read the workspace-root ``.agent/artifact-formats/plan.md`` body.

    The package also ships its own bundled copy under
    ``ralph-workflow/.agent/...`` so we must skip past that copy and
    pin to the workspace root. Walk upward from the test file and
    return the first parent that hosts ``.agent/`` but does NOT host
    a ``pyproject.toml`` (the Python package lives at
    ``ralph-workflow/`` and owns both). The prior first-match parent
    walk returned ``ralph-workflow/.agent/artifact-formats/plan.md``
    instead of the workspace-root copy.
    """
    repo_root = Path(__file__).resolve()
    for parent in repo_root.parents:
        if not (parent / ".agent").exists():
            continue
        if (parent / "pyproject.toml").exists():
            continue
        candidate = parent / ".agent" / "artifact-formats" / "plan.md"
        if candidate.exists():
            return candidate.read_text(encoding="utf-8")
    pytest.fail("could not locate workspace-root .agent/artifact-formats/plan.md on disk")


@pytest.mark.timeout_seconds(5)
def test_canonical_validator_errors_section_present() -> None:
    """``plan.md`` must contain the required ``## Canonical validator errors to fix`` H2."""
    body = _load_plan_md()
    assert "## Canonical validator errors to fix" in body, (
        "plan.md must contain the '## Canonical validator errors to fix' "
        "H2 so agents can map validator error strings to fixes"
    )


@pytest.mark.timeout_seconds(5)
def test_canonical_validator_errors_section_ordered_between_dumb_proof_and_module_family() -> (
    None
):
    """The new H2 must sit between ``## Dumb-proof checklist`` and ``## Module family``."""
    body = _load_plan_md()
    dumb_proof_idx = body.index("## Dumb-proof checklist")
    canonical_idx = body.index("## Canonical validator errors to fix")
    module_family_idx = body.index("## Module family")
    assert dumb_proof_idx < canonical_idx < module_family_idx, (
        "## Canonical validator errors to fix must appear AFTER "
        "## Dumb-proof checklist and BEFORE ## Module family"
    )


@pytest.mark.timeout_seconds(5)
def test_canonical_validator_errors_section_lists_required_error_strings() -> None:
    """The new H2 must enumerate every literal cross-section validator error string.

    The strings are copied verbatim from
    ``ralph/mcp/artifacts/plan/_validation.py`` lines 173/229/239/251/732/681/758/769/796.
    """
    body = _load_plan_md()
    canonical_idx = body.index("## Canonical validator errors to fix")
    module_family_idx = body.index("## Module family")
    section_body = body[canonical_idx:module_family_idx]
    required_strings = (
        "plan step depends_on cycle detected at step",
        "plan cannot declare both parallel_plan and work_units; pick one",
        "verification method must not invoke a shell interpreter directly",
        "satisfied_by_steps cannot reference a research or verify step",
        "skills_mcp.skills must contain at least one skill name",
        "acceptance criterion",
        "references unknown step number",
        "plan envelope has no valid 'content' object",
        "plan payload must decode to a JSON object",
        "plan draft is missing a 'sections' object",
    )
    for required in required_strings:
        assert required in section_body, (
            f"## Canonical validator errors to fix must contain literal "
            f"validator error substring {required!r}"
        )


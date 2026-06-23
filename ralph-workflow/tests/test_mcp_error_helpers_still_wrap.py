"""No-skill MCP-error-helper regression tests.

The skill bodies in ``submit-plan-artifact`` and ``submit-plan-step-edits``
are user-facing companions to a no-skill MCP error-helper layer. The
helpers wrap raw validator exceptions in a structured message that
points the agent at the format doc and at the matching skill. These
tests assert that the no-skill wrap stays complete even as the
skill bodies evolve \u2014 a helper that drops a pointer silently
regresses an agent without the skill.

The tests run in-process (no real I/O, no subprocess, no ``time.sleep``)
and complete in well under one second each.
"""

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import cast

import pytest

from ralph.mcp.artifacts.file_backend import DEFAULT_FILE_BACKEND
from ralph.mcp.tools.artifact import (
    _format_plan_section_submission_error,
    _format_plan_step_edit_error,
)


@pytest.mark.timeout_seconds(5)
def test_format_plan_section_submission_error_wraps_canonical_envelope() -> None:
    """Section-submission helper wraps a missing-scope_items detail with all four pointers.

    When a cheap model submits ``summary`` without the required
    ``scope_items`` list, ``_format_plan_section_submission_error`` MUST
    return a string that mentions the format doc reference, the
    canonical fix path, and the bundled skill, so the agent can
    self-correct without re-reading the source code.
    """
    message = _format_plan_section_submission_error(
        section="summary",
        mode="replace",
        detail="summary.scope_items: Field required",
        workspace_root=Path("/tmp"),
        backend=DEFAULT_FILE_BACKEND,
        tool_name="ralph_submit_plan_section",
    )
    assert "Fix this by reading" in message
    assert ".agent/artifact-formats/plan.md" in message
    assert "Optional: the bundled `submit-plan-artifact` skill" in message
    assert "scope_items" in message


@pytest.mark.timeout_seconds(5)
def test_format_plan_step_edit_error_mentions_submit_plan_step_edits() -> None:
    """Step-edit helper names the skill and every step-mutation tool.

    When a cheap model submits a malformed ``ralph_insert_plan_step``
    payload, ``_format_plan_step_edit_error`` MUST name the
    ``submit-plan-step-edits`` skill AND every step-mutation tool name
    that the agent might retry against. Without the skill pointer the
    agent has no fast-path to the retry-envelope table.
    """
    malformed_payload: dict[str, object] = {
        "step": {"title": "no index or step_number, ambiguous tool call"}
    }
    detail = (
        "step edit failed: expected either 'index' (ralph_insert_plan_step) "
        f"or 'step_number' (replace/patch/remove); got {json.dumps(malformed_payload)}"
    )
    message = _format_plan_step_edit_error(
        detail=detail,
        workspace_root=Path("/tmp"),
        backend=DEFAULT_FILE_BACKEND,
        tool_name="ralph_insert_plan_step",
    )
    assert "submit-plan-step-edits" in message
    mutation_tools = cast(
        "tuple[str, ...]",
        (
            "ralph_insert_plan_step",
            "ralph_replace_plan_step",
            "ralph_patch_step",
            "ralph_remove_plan_step",
            "ralph_move_plan_step",
        ),
    )
    for tool in mutation_tools:
        assert tool in message, (
            f"_format_plan_step_edit_error must name {tool!r} so the agent "
            f"knows which tool the envelope belongs to"
        )


@pytest.mark.timeout_seconds(5)
def test_planning_jinja_preserves_source_of_truth_wording() -> None:
    """``planning.jinja`` must keep the canonical source-of-truth pointer.

    The prompt template references the format doc as the source of truth
    and points the agent at the bundled skill. If a refactor drops
    either substring the planning prompt silently loses the pointer
    and a cheap model retries against stale schema. Lock the wording.
    """
    repo_root = Path(__file__).resolve()
    template_path: Path | None = None
    for parent in repo_root.parents:
        candidate = parent / "ralph" / "prompts" / "templates" / "planning.jinja"
        if candidate.exists():
            template_path = candidate
            break
    assert template_path is not None, "could not locate planning.jinja on disk"
    body = template_path.read_text(encoding="utf-8")
    assert "source of truth" in body
    assert ".agent/artifact-formats/plan.md" in body


def _locate_plan_md() -> Path:
    """Locate the workspace-root ``.agent/artifact-formats/plan.md``.

    PLAN step 4 mandates the ``## Canonical validator errors to fix``
    H2 in the workspace-root artifact-format doc. The package also
    ships its own bundled copy under ``ralph-workflow/.agent/...``
    so the test must skip past that copy and pin to the workspace
    root. We resolve the test file path and walk upward until we find
    a parent that hosts ``.agent/`` but does NOT host a ``pyproject.toml``
    (the Python package lives at ``ralph-workflow/`` and owns both).
    This pins the assertion to the workspace-root copy and prevents
    the prior bug where the first-match parent returned
    ``ralph-workflow/.agent/artifact-formats/plan.md`` instead.
    """
    repo_root = Path(__file__).resolve()
    for parent in repo_root.parents:
        if not (parent / ".agent").exists():
            continue
        if (parent / "pyproject.toml").exists():
            continue
        candidate = parent / ".agent" / "artifact-formats" / "plan.md"
        if candidate.exists():
            return candidate
    raise AssertionError(
        "could not locate workspace-root .agent/artifact-formats/plan.md on disk"
    )


@pytest.mark.timeout_seconds(5)
def test_plan_md_has_canonical_validator_errors_to_fix_heading() -> None:
    """``.agent/artifact-formats/plan.md`` must carry the cross-section error index.

    PLAN step 4 mandates a new ``## Canonical validator errors to fix``
    H2 placed *between* ``## Dumb-proof checklist`` and
    ``## Module family``. Cheap-model agents use that index to map any
    Pydantic / cross-section rejection message back to the literal
    error string emitted by ``_validation.py``. If the H2 is missing,
    the agent retries against a stale schema and the helper wrap cannot
    shorten the loop. Lock the heading existence AND its position so a
    silent rename or re-order cannot regress the documentation surface.
    """
    plan_md_path = _locate_plan_md()
    body = plan_md_path.read_text(encoding="utf-8")
    h2_titles: list[str] = [
        match.group(2).strip()
        for match in re.finditer(r"^(#{2,6}) (.+?)\s*$", body, flags=re.MULTILINE)
    ]
    assert "## Canonical validator errors to fix" in body, (
        "PLAN step 4 requires .agent/artifact-formats/plan.md to expose the "
        "## Canonical validator errors to fix H2 that maps every literal "
        "cross-section validator message to its canonical fix"
    )
    dumb_idx = h2_titles.index("Dumb-proof checklist")
    canonical_idx = h2_titles.index("Canonical validator errors to fix")
    module_idx = h2_titles.index("Module family")
    assert dumb_idx < canonical_idx < module_idx, (
        "## Canonical validator errors to fix must appear between "
        "## Dumb-proof checklist and ## Module family; got "
        f"dumb_idx={dumb_idx}, canonical_idx={canonical_idx}, module_idx={module_idx}"
    )

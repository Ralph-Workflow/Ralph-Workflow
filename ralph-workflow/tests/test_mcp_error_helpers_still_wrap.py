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

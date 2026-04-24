"""Tests: missing-artifact retry contract — hint written on failure, consumed on retry.

Covers the contract:
1. When a phase handler cannot find its required artifact, it writes a retry
   hint to .agent/tmp/last_retry_error_{phase}.txt
2. When materialize_prompt is called for the same phase, it reads the hint,
   surfaces it as LAST_RETRY_ERROR, and deletes the file so it doesn't leak
3. Parameterized over REQUIRED_ARTIFACTS so new phases are auto-covered
4. The development phase writes a "missing input" hint (not a "missing output"
   hint) because the plan is a planning-phase output, not a development submission.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import MagicMock, patch

import pytest

import ralph.prompts.materialize as materialize_module
from ralph.phases.development import handle_development, handle_development_analysis
from ralph.phases.fix import handle_fix
from ralph.phases.planning import handle_planning
from ralph.phases.required_artifacts import REQUIRED_ARTIFACTS, build_retry_hint, retry_hint_path
from ralph.phases.review import handle_review, handle_review_analysis
from ralph.pipeline.effects import InvokeAgentEffect
from ralph.pipeline.events import PhaseFailureEvent
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import _read_and_clear_retry_hint
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

_PHASE_TO_HANDLER = {
    "planning": handle_planning,
    "development": handle_development,
    "development_analysis": handle_development_analysis,
    "review": handle_review,
    "review_analysis": handle_review_analysis,
    "fix": handle_fix,
}


def _make_ctx(workspace: MemoryWorkspace) -> MagicMock:
    ctx = MagicMock()
    ctx.workspace = workspace
    return ctx


def _invoke_effect() -> MagicMock:
    return MagicMock(spec=InvokeAgentEffect)


@pytest.mark.parametrize(
    "phase",
    [p for p in REQUIRED_ARTIFACTS if p not in {"planning", "development"}],
)
def test_missing_artifact_writes_retry_hint(phase: str) -> None:
    workspace = MemoryWorkspace()
    ctx = _make_ctx(workspace)

    handler = _PHASE_TO_HANDLER[phase]
    events = handler(_invoke_effect(), ctx)

    failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
    assert failure_events, f"Expected PhaseFailureEvent from {phase} when artifact is missing"
    assert failure_events[0].recoverable is True

    hint_path = retry_hint_path(phase)
    assert workspace.exists(hint_path), (
        f"Retry hint was not written to {hint_path} after {phase} failed to find artifact"
    )
    hint_content = workspace.read(hint_path)
    assert len(hint_content) > 0, "Retry hint must not be empty"


def test_planning_missing_plan_artifact_writes_retry_hint() -> None:
    workspace = MemoryWorkspace()
    ctx = _make_ctx(workspace)

    events = handle_planning(_invoke_effect(), ctx)

    failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
    assert failure_events, "Expected PhaseFailureEvent from planning when plan artifact is missing"
    assert failure_events[0].recoverable is True

    hint_path = retry_hint_path("planning")
    assert workspace.exists(hint_path)


def test_development_missing_plan_artifact_writes_retry_hint() -> None:
    workspace = MemoryWorkspace()
    ctx = _make_ctx(workspace)

    events = handle_development(_invoke_effect(), ctx)

    failure_events = [e for e in events if isinstance(e, PhaseFailureEvent)]
    assert failure_events
    assert failure_events[0].recoverable is True

    hint_path = retry_hint_path("development")
    assert workspace.exists(hint_path)


def test_development_missing_plan_hint_names_upstream_planning_phase() -> None:
    """Development retry hint must say the PLANNING phase is the producer, not development.

    The plan artifact is a planning-phase *output* / development-phase *input*.
    The retry hint must not claim the development agent forgot to submit it.
    """
    workspace = MemoryWorkspace()
    ctx = _make_ctx(workspace)

    handle_development(_invoke_effect(), ctx)

    hint_content = workspace.read(retry_hint_path("development"))
    # Must reference the upstream producer, not blame the development agent
    assert "planning" in hint_content.lower(), (
        "Development retry hint must name the 'planning' phase as the upstream producer"
    )
    # Must NOT contain wording that implies development should submit the plan
    assert "did not submit" not in hint_content, (
        "Development retry hint must not claim the development agent failed to submit the plan"
    )
    # Must indicate this is a missing input (not missing output)
    assert "input" in hint_content.lower() or "PIPELINE INPUT MISSING" in hint_content, (
        "Development retry hint must indicate the plan is a missing upstream input"
    )


def test_read_and_clear_retry_hint_returns_content_and_deletes_file() -> None:
    workspace = MemoryWorkspace()
    workspace.write(retry_hint_path("review"), "hint content here")

    result = _read_and_clear_retry_hint(workspace, "review")

    assert result == "hint content here"
    assert not workspace.exists(retry_hint_path("review")), "Hint file must be deleted after read"


def test_read_and_clear_retry_hint_returns_empty_when_absent() -> None:
    workspace = MemoryWorkspace()
    result = _read_and_clear_retry_hint(workspace, "review")
    assert result == ""


@pytest.mark.parametrize(
    "phase",
    [p for p in REQUIRED_ARTIFACTS if p not in {"planning", "development"}],
)
def test_retry_hint_content_includes_artifact_info(phase: str) -> None:
    ra = REQUIRED_ARTIFACTS[phase]
    hint = build_retry_hint(phase, "the agent forgot to submit")
    assert ra.artifact_type in hint, f"Hint for {phase} must mention artifact type"
    assert ra.json_path in hint, f"Hint for {phase} must mention artifact path"


def test_materialize_review_prompt_includes_last_retry_error(tmp_path: Path) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "fix the bug")
    workspace.write(retry_hint_path("review"), "PREVIOUS ATTEMPT FAILED: missing issues.json")

    with patch.object(materialize_module, "_git_diff", return_value="diff --git a/x.py"):
        prompt_path = materialize_module.materialize_prompt_for_phase(
            phase="review",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.REVIEW),
            workspace_root=tmp_path,
        )

    rendered = workspace.read(prompt_path)
    assert "PREVIOUS ATTEMPT FAILED" in rendered, (
        "Rendered review prompt must include LAST_RETRY_ERROR content when hint file exists"
    )
    assert not workspace.exists(retry_hint_path("review")), (
        "Hint file must be deleted after being read by materialize"
    )


def test_materialize_development_analysis_prompt_includes_last_retry_error(
    tmp_path: Path,
) -> None:
    from ralph.phases.required_artifacts import DEV_RESULT_ARTIFACT_JSON_PATH  # noqa: PLC0415

    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "implement the plan")
    workspace.write(
        DEV_RESULT_ARTIFACT_JSON_PATH,
        json.dumps({
            "type": "development_result",
            "content": {"status": "completed", "summary": "Done.", "files_changed": "- a.py"},
        }),
    )
    workspace.write(
        retry_hint_path("development_analysis"),
        "PREVIOUS ATTEMPT FAILED: missing decision artifact",
    )

    with patch.object(materialize_module, "_git_diff", return_value="diff"):
        prompt_path = materialize_module.materialize_prompt_for_phase(
            phase="development_analysis",
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
            workspace_root=tmp_path,
        )

    rendered = workspace.read(prompt_path)
    assert "PREVIOUS ATTEMPT FAILED" in rendered
    assert not workspace.exists(retry_hint_path("development_analysis"))

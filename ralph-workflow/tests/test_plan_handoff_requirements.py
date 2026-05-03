from __future__ import annotations

import json
from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

import ralph.prompts.materialize as materialize_module
from ralph.policy.loader import load_policy
from ralph.prompts.materialize import materialize_prompt_for_phase
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

_MINIMAL_DEVELOPMENT_RESULT = json.dumps(
    {
        "type": "development_result",
        "content": {
            "status": "completed",
            "summary": "Implemented the requested change.",
            "files_changed": "- ralph/prompts/materialize.py",
        },
    }
)

_MINIMAL_ISSUES = json.dumps(
    {
        "type": "issues",
        "content": {
            "status": "issues_found",
            "summary": "A follow-up fix is required.",
            "issues": [
                {
                    "path": "ralph/prompts/materialize.py",
                    "severity": "medium",
                    "summary": "Need a stricter precondition.",
                }
            ],
            "what_came_up_short": ["Plan handoff handling is too loose."],
            "how_to_fix": ["Require an existing plan handoff before non-planning renders."],
        },
    }
)

_MINIMAL_PLANNING_ANALYSIS_DECISION = json.dumps(
    {
        "type": "planning_analysis_decision",
        "content": {
            "status": "request_changes",
            "summary": "Revise the plan.",
            "what_came_up_short": ["Verification is too vague."],
            "how_to_fix": ["Edit the existing plan instead of starting over."],
        },
    }
)


@pytest.mark.parametrize(
    ("phase", "previous_phase"),
    [
        ("planning", "planning_analysis"),
        ("planning_analysis", None),
        ("development", None),
        ("development_analysis", None),
        ("review", None),
        ("review_analysis", None),
        ("fix", None),
    ],
)
def test_non_new_plan_prompts_require_existing_plan_handoff(
    tmp_path: Path,
    phase: str,
    previous_phase: str | None,
) -> None:
    policy = load_policy(tmp_path / ".agent")
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Tighten the plan handoff rules.")

    if phase in {"review", "development_analysis"}:
        workspace.write(
            ".agent/artifacts/development_result.json",
            _MINIMAL_DEVELOPMENT_RESULT,
        )
    if phase in {"review_analysis"}:
        workspace.write(".agent/artifacts/issues.json", _MINIMAL_ISSUES)
    if previous_phase == "planning_analysis":
        workspace.write(
            ".agent/artifacts/planning_analysis_decision.json",
            _MINIMAL_PLANNING_ANALYSIS_DECISION,
        )

    drain = {
        "planning": SessionDrain.PLANNING,
        "planning_analysis": SessionDrain.PLANNING,
        "development": SessionDrain.DEVELOPMENT,
        "development_analysis": SessionDrain.DEVELOPMENT,
        "review": SessionDrain.REVIEW,
        "review_analysis": SessionDrain.REVIEW,
        "fix": SessionDrain.FIX,
    }[phase]

    with (
        patch.object(materialize_module, "_git_diff", return_value="diff"),
        pytest.raises(ValueError, match=r"\.agent/PLAN\.md"),
    ):
        materialize_prompt_for_phase(
            phase=phase,
            workspace=workspace,
            pipeline_policy=policy.pipeline,
            artifacts_policy=policy.artifacts,
            session_caps=SessionCapabilities.defaults_for_drain(drain),
            workspace_root=tmp_path,
            previous_phase=previous_phase,
        )

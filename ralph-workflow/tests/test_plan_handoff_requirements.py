from __future__ import annotations

from typing import TYPE_CHECKING
from unittest.mock import patch

import pytest

import ralph.prompts.materialize as materialize_module
from ralph.policy.loader import load_policy
from ralph.policy.models import (
    ArtifactsPolicy,
    PhaseDefinition,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities, SessionDrain
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

_MINIMAL_DEVELOPMENT_RESULT = (
    "---\n"
    "type: development_result\n"
    "status: completed\n"
    "---\n\n"
    "## Summary\n\n- [SUM-1] Implemented the requested change.\n\n"
    "## Files Changed\n\n- [F-1] ralph/prompts/materialize.py\n"
)


_MINIMAL_PLANNING_ANALYSIS_DECISION = (
    "---\n"
    "type: planning_analysis_decision\n"
    "status: request_changes\n"
    "---\n\n"
    "## Summary\n\n- [SUM-1] Revise the plan.\n\n"
    "## What Came Up Short\n\n- [GAP-1] Verification is too vague.\n\n"
    "## How To Fix\n\n- [FIX-1] Edit the existing plan instead of starting over.\n"
)


@pytest.mark.parametrize(
    ("phase", "previous_phase"),
    [
        ("planning", "planning_analysis"),
        ("planning_analysis", None),
        ("development", None),
        ("development_analysis", None),
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

    if phase in {"development_analysis"}:
        workspace.write(
            ".agent/artifacts/development_result.md",
            _MINIMAL_DEVELOPMENT_RESULT,
        )
    if previous_phase == "planning_analysis":
        workspace.write(
            ".agent/artifacts/planning_analysis_decision.md",
            _MINIMAL_PLANNING_ANALYSIS_DECISION,
        )

    drain = {
        "planning": SessionDrain.PLANNING,
        "planning_analysis": SessionDrain.PLANNING,
        "development": SessionDrain.DEVELOPMENT,
        "development_analysis": SessionDrain.DEVELOPMENT,
    }[phase]

    with (
        patch.object(materialize_module, "_git_diff", return_value="diff"),
        pytest.raises(ValueError, match=r"\.agent/PLAN\.md"),
    ):
        materialize_prompt_for_phase(
            PromptPhaseContext(
                phase=phase,
                workspace=workspace,
                pipeline_policy=policy.pipeline,
                session_caps=SessionCapabilities.defaults_for_drain(drain),
                workspace_root=tmp_path,
            ),
            PromptPhaseOptions(
                artifacts_policy=policy.artifacts,
                previous_phase=previous_phase,
            ),
        )


def test_review_role_requires_existing_plan_handoff(tmp_path: Path) -> None:
    """A custom review-role phase bound to review.jinja must require an existing plan.

    review.jinja is never part of the default pipeline, so the default policy
    cannot cover it. This test constructs a minimal custom policy and verifies
    that prompt materialization raises the expected MissingPlanHandoffError when
    no .agent/PLAN.md (or .agent/artifacts/plan.md) is present.
    """
    pipeline_policy = PipelinePolicy(
        phases={
            "review": PhaseDefinition(
                drain="review",
                role="review",
                prompt_template="review.jinja",
                transitions=PhaseTransition(on_success="complete"),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                transitions=PhaseTransition(on_success="complete", on_loopback="complete"),
            ),
        },
        entry_phase="review",
        terminal_phase="complete",
    )
    artifacts_policy = ArtifactsPolicy(artifacts={})
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "Review the implementation.")

    with (
        patch.object(materialize_module, "_git_diff", return_value="diff"),
        pytest.raises(ValueError, match=r"\.agent/PLAN\.md"),
    ):
        materialize_prompt_for_phase(
            PromptPhaseContext(
                phase="review",
                workspace=workspace,
                pipeline_policy=pipeline_policy,
                session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.REVIEW),
                workspace_root=tmp_path,
            ),
            PromptPhaseOptions(
                artifacts_policy=artifacts_policy,
            ),
        )

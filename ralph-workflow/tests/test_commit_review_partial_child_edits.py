from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from ralph.agents.invoke.completion_quiesce import record_partial_child_edits
from ralph.mcp.protocol.capability_mapping import SessionDrain
from ralph.policy.models import (
    ArtifactsPolicy,
    LoopCounterConfig,
    PhaseDefinition,
    PhaseLoopPolicy,
    PhaseTransition,
    PipelinePolicy,
)
from ralph.prompts.materialize import (
    PromptPhaseContext,
    PromptPhaseOptions,
    materialize_prompt_for_phase,
)
from ralph.prompts.types import SessionCapabilities
from ralph.workspace.memory import MemoryWorkspace

if TYPE_CHECKING:
    from pathlib import Path

pytestmark = pytest.mark.timeout_seconds(10)


def _render(tmp_path: Path) -> str:
    policy = PipelinePolicy(
        phases={
            "development_commit_review": PhaseDefinition(
                drain="commit",
                role="commit_review",
                prompt_template="commit_review.jinja",
                transitions=PhaseTransition(
                    on_success="complete",
                    on_loopback="development_commit_review",
                    on_failure="failed_terminal",
                ),
                loop_policy=PhaseLoopPolicy(iteration_state_field="commit_review_iteration"),
            ),
            "complete": PhaseDefinition(
                drain="complete",
                role="terminal",
                terminal_outcome="success",
                transitions=PhaseTransition(on_success="complete"),
            ),
        },
        entry_phase="development_commit_review",
        terminal_phase="complete",
        loop_counters={"commit_review_iteration": LoopCounterConfig(default_max=3)},
    )
    workspace = MemoryWorkspace(root=str(tmp_path))
    workspace.write("PROMPT.md", "x")
    workspace.write(".agent/PLAN.md", "# Plan")
    context = PromptPhaseContext(
        phase="development_commit_review",
        workspace=workspace,
        pipeline_policy=policy,
        session_caps=SessionCapabilities.defaults_for_drain(SessionDrain.DEVELOPMENT),
        workspace_root=tmp_path,
    )
    return workspace.read(
        materialize_prompt_for_phase(
            context,
            PromptPhaseOptions(artifacts_policy=ArtifactsPolicy(artifacts={}), previous_phase=None),
        )
    )


def test_the_commit_reviewer_is_told_when_child_edits_may_be_partial(tmp_path: Path) -> None:
    record_partial_child_edits(tmp_path, agent="opencode/glm", children=("ses_U4", "ses_U8"))

    rendered = _render(tmp_path)

    assert "Possibly partial edits" in rendered
    assert "ses_U4" in rendered
    assert "ses_U8" in rendered


def test_no_partial_edit_note_without_torn_down_children(tmp_path: Path) -> None:
    assert "Possibly partial edits" not in _render(tmp_path)
